from __future__ import annotations

import asyncio
import html
from threading import Lock
from typing import NamedTuple

from telegram.constants import ParseMode

from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from messenger.contracts import ProgressEvent
from messenger.profile_service import cleanup_product_result
from messenger.windgram_service import (
    PARAM_CAPTIONS,
    PARAM_NAMES,
    ParsedWindgramInput,
    build_windgram_product_result,
    parse_windgram_input,
    windgram_repeat_command,
)
from telegram_file_send import reply_png_file
from user_location_session import remember_location
from windgram_product import WindgramData, windgram_leads


class ParsedWindgramRequest(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    top_hpa: int
    param: str


def parse_windgram_request(raw_text: str) -> ParsedWindgramRequest:
    parsed: ParsedWindgramInput = parse_windgram_input(raw_text)
    return ParsedWindgramRequest(
        parsed.location_query,
        parsed.run,
        parsed.lead_from,
        parsed.lead_to,
        parsed.step,
        parsed.top_hpa,
        parsed.param,
    )


def _lead_step(data: WindgramData) -> int:
    return data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0


def format_windgram_caption(data: WindgramData) -> str:
    return (
        f"🟦 Windgram GFS 0.25 · {PARAM_NAMES.get(data.param, data.param)}\n"
        f"🕒 {data.run.date} {data.run.cycle}Z · UTC · +{data.leads[0]}…+{data.leads[-1]} ч · шаг {_lead_step(data)} ч\n"
        f"📍 Узел GFS: {data.grid_lat:.3f}, {data.grid_lon:.3f}\n"
        f"📊 {PARAM_CAPTIONS.get(data.param, PARAM_CAPTIONS['wind'])}\n"
        "PNG ниже. Команда для повтора — отдельным сообщением."
    )


def format_windgram_file_caption(data: WindgramData) -> str:
    return f"PNG · WINDGRAM · {PARAM_NAMES.get(data.param, data.param)} · GFS {data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · UTC"


def repeat_windgram_command(point: GeoPoint, parsed: ParsedWindgramRequest, run: GfsRun) -> str:
    return windgram_repeat_command(
        point,
        run=run,
        lead_from=parsed.lead_from,
        lead_to=parsed.lead_to,
        step=parsed.step,
        top_hpa=parsed.top_hpa,
        param=parsed.param,
    )


def format_repeat_windgram_message(point: GeoPoint, parsed: ParsedWindgramRequest, run: GfsRun) -> str:
    command = html.escape(repeat_windgram_command(point, parsed, run))
    return "📋 Повторить этот расчёт:\n" f"<code>{command}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


def _progress_text(point: GeoPoint, parsed: ParsedWindgramRequest, event: ProgressEvent) -> str:
    data = dict(event.data)
    header = (
        "⏳ Срок × уровень GFS\n"
        f"📍 {point.label}\n"
        f"+{parsed.lead_from}…+{parsed.lead_to} ч · шаг {parsed.step} ч\n"
    )
    if event.stage in {"check", "run"}:
        body = "1/6 Проверяю опубликованный цикл GFS…"
        if event.stage == "run" and data.get("run_date") and data.get("run_cycle"):
            body = f"1/6 GFS {data['run_date']} {data['run_cycle']}Z"
    elif event.stage == "grid":
        body = f"2/6 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage == "windgram_step":
        current = data.get("index") or event.current
        total = data.get("total") or event.total
        lead = data.get("lead_hour")
        suffix = f" · +{lead} ч" if lead is not None else ""
        body = f"3/6 Загружаю сроки: {current}/{total}{suffix}" if current and total else "3/6 Загружаю сроки…"
    elif event.stage in {"download_start", "download", "download_done", "cache"}:
        body = "3/6 Загружаю модельные данные…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/6 Читаю профили и формирую матрицу…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/6 Формирую PNG…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


async def run_windgram_product(message, point: GeoPoint, parsed: ParsedWindgramRequest, gfs_semaphore) -> bool:
    """Render the common windgram service for Telegram."""

    status = await message.reply_text(
        "⏳ Срок × уровень GFS\n"
        f"📍 {point.label}\n"
        f"+{parsed.lead_from}…+{parsed.lead_to} ч · шаг {parsed.step} ч\n"
        "1/6 Проверяю опубликованный цикл GFS…"
    )
    state = {"event": ProgressEvent(stage="check", message="Проверяю данные")}
    lock = Lock()
    stop = asyncio.Event()
    last_text = ""
    common_result = None

    def progress(event: ProgressEvent) -> None:
        with lock:
            state["event"] = event

    async def reporter() -> None:
        nonlocal last_text
        while not stop.is_set():
            with lock:
                event = state["event"]
            text = _progress_text(point, parsed, event)
            if text != last_text:
                try:
                    await status.edit_text(text)
                    last_text = text
                except Exception:
                    pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                pass

    reporter_task = asyncio.create_task(reporter())
    try:
        async with gfs_semaphore:
            common_result = await asyncio.to_thread(
                build_windgram_product_result,
                point,
                parsed.lead_from,
                parsed.lead_to,
                parsed.step,
                parsed.top_hpa,
                parsed.param,
                parsed.run,
                progress_callback=progress,
                run_selector=latest_available_run_for_lead,
            )
        stop.set()
        await reporter_task
        await status.edit_text(common_result.summary)

        leads = windgram_leads(parsed.lead_from, parsed.lead_to, parsed.step)
        for attachment in common_result.attachments:
            if attachment.kind == "image":
                await reply_png_file(
                    message,
                    attachment.path,
                    caption=attachment.caption,
                    prefer_photo=len(leads) <= 12,
                )
            else:
                with attachment.path.open("rb") as file_obj:
                    await message.reply_document(
                        document=file_obj,
                        filename=attachment.filename,
                        caption=attachment.caption,
                    )

        if common_result.repeat_command:
            await message.reply_text(
                f"📋 <code>{html.escape(common_result.repeat_command)}</code>",
                parse_mode=ParseMode.HTML,
            )
        return True
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        stop.set()
        await reporter_task
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        stop.set()
        await reporter_task
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        stop.set()
        if not reporter_task.done():
            await reporter_task
        if common_result is not None:
            cleanup_product_result(common_result)
    return False


async def resolve_windgram_request(message, raw: str, gfs_semaphore, geocode_semaphore, user_id: int = 0) -> bool:
    try:
        parsed = parse_windgram_request(raw)
        async with geocode_semaphore:
            candidates = await asyncio.to_thread(search_location_candidates, parsed.location_query, 3)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return False

    if not candidates:
        await message.reply_text("Точка не найдена. Пришлите координаты, город или геолокацию Telegram.")
        return False
    if len(candidates) > 1:
        labels = "\n".join(f"{i + 1}. {point.label}" for i, point in enumerate(candidates[:3]))
        await message.reply_text(
            "Найдено несколько точек. Уточните запрос или используйте координаты.\n\n"
            f"Пример:\n/windgram {candidates[0].label} to={parsed.lead_to} step={parsed.step} param={parsed.param}\n\n"
            f"Варианты:\n{labels}"
        )
        return False

    remember_location(user_id, candidates[0])
    return await run_windgram_product(message, candidates[0], parsed, gfs_semaphore)
