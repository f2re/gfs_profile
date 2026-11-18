from __future__ import annotations

import asyncio
import html
from threading import Lock
from typing import NamedTuple

from telegram.constants import ParseMode

from cloudgram_product import CloudgramData, cloudgram_leads
from geocode import GeocodeError, GeoPoint
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from messenger.cloudgram_service import (
    MODE_DESCRIPTIONS,
    MODE_TITLES,
    ParsedCloudgramInput,
    build_cloudgram_product_result,
    cloudgram_repeat_command,
    hazard_label,
    normalize_cloudgram_mode,
    parse_cloudgram_input,
)
from messenger.contracts import ProgressEvent
from messenger.profile_service import cleanup_product_result
from telegram_file_send import reply_png_file
from user_location_session import remember_location


class ParsedCloudgramRequest(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    mode: str = "pro"


def parse_cloudgram_request(raw_text: str) -> ParsedCloudgramRequest:
    parsed: ParsedCloudgramInput = parse_cloudgram_input(raw_text)
    return ParsedCloudgramRequest(
        parsed.location_query,
        parsed.run,
        parsed.lead_from,
        parsed.lead_to,
        parsed.step,
        parsed.mode,
    )


def _mode_title(mode: str) -> str:
    return "SIMPLE" if normalize_cloudgram_mode(mode) == "simple" else "PRO"


def _mode_description(mode: str) -> str:
    return MODE_DESCRIPTIONS[normalize_cloudgram_mode(mode)]


def _hazard_label(value: int) -> str:
    return hazard_label(value)


def _lead_step(data: CloudgramData) -> int:
    return data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0


def format_cloudgram_caption(data: CloudgramData, mode: str = "pro") -> str:
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    missing = f"\n⚠️ Нет полей GFS: {', '.join(data.missing_fields)}" if data.missing_fields else ""
    return (
        f"☁️ Cloudgram GFS 0.25 · {_mode_title(mode)}\n"
        f"🕒 {data.run.date} {data.run.cycle}Z · UTC · +{data.leads[0]}…+{data.leads[-1]} ч · шаг {_lead_step(data)} ч\n"
        f"📍 Узел GFS: {data.grid_lat:.3f}, {data.grid_lon:.3f}\n"
        f"📊 {_mode_description(mode)}\n"
        f"⚠️ Макс. опасность: {_hazard_label(max_hazard)}"
        f"{missing}\n"
        "PNG ниже. Команда для повтора — отдельным сообщением."
    )


def format_cloudgram_file_caption(data: CloudgramData, mode: str = "pro") -> str:
    return f"PNG · {_mode_title(mode)} · GFS {data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · UTC"


def repeat_cloudgram_command(point: GeoPoint, parsed: ParsedCloudgramRequest, run: GfsRun) -> str:
    return cloudgram_repeat_command(
        point,
        run=run,
        lead_from=parsed.lead_from,
        lead_to=parsed.lead_to,
        step=parsed.step,
        mode=parsed.mode,
    )


def format_repeat_cloudgram_message(point: GeoPoint, parsed: ParsedCloudgramRequest, run: GfsRun) -> str:
    command = html.escape(repeat_cloudgram_command(point, parsed, run))
    return "📋 Повторить этот расчёт:\n" f"<code>{command}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


def _progress_text(point: GeoPoint, parsed: ParsedCloudgramRequest, event: ProgressEvent) -> str:
    data = dict(event.data)
    header = (
        "⏳ Облака и явления GFS\n"
        f"📍 {point.label}\n"
        f"+{parsed.lead_from}…+{parsed.lead_to} ч · шаг {parsed.step} ч · {MODE_TITLES[parsed.mode]}\n"
    )
    if event.stage in {"check", "run"}:
        body = "1/6 Проверяю опубликованный цикл GFS…"
        if event.stage == "run" and data.get("run_date") and data.get("run_cycle"):
            body = f"1/6 GFS {data['run_date']} {data['run_cycle']}Z"
    elif event.stage == "grid":
        body = f"2/6 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage in {"cloudgram_step", "download_start", "download", "download_done", "cache"}:
        current = data.get("index") or event.current
        total = data.get("total") or event.total
        lead = data.get("lead_hour")
        suffix = f" · +{lead} ч" if lead is not None else ""
        body = f"3/6 Загружаю сроки: {current}/{total}{suffix}" if current and total else "3/6 Загружаю модельные поля…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/6 Считаю облачность, явления и риски…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/6 Формирую PNG…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


async def run_cloudgram_product(message, point: GeoPoint, parsed: ParsedCloudgramRequest, gfs_semaphore) -> bool:
    status = await message.reply_text(
        "⏳ Облака и явления GFS\n"
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
                build_cloudgram_product_result,
                point,
                parsed.lead_from,
                parsed.lead_to,
                parsed.step,
                parsed.mode,
                parsed.run,
                progress_callback=progress,
                run_selector=latest_available_run_for_lead,
            )
        stop.set()
        await reporter_task
        await status.edit_text(common_result.summary)
        leads = cloudgram_leads(parsed.lead_from, parsed.lead_to, parsed.step)
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
                    await message.reply_document(document=file_obj, filename=attachment.filename, caption=attachment.caption)
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


async def resolve_cloudgram_request(message, raw: str, gfs_semaphore, geocode_semaphore, user_id: int = 0) -> bool:
    try:
        parsed = parse_cloudgram_request(raw)
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
            f"Пример:\n/cloudgram {candidates[0].label} to={parsed.lead_to} step={parsed.step} mode={parsed.mode}\n\n"
            f"Варианты:\n{labels}"
        )
        return False

    remember_location(user_id, candidates[0])
    return await run_cloudgram_product(message, candidates[0], parsed, gfs_semaphore)
