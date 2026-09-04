from __future__ import annotations

import asyncio
import html
from threading import Lock
from typing import NamedTuple

from telegram import InputFile
from telegram.constants import ParseMode

from aero_product import format_aero_caption
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun
from messenger.aero_service import (
    AERO_DIAGRAM_TYPE,
    ParsedAeroInput,
    build_aero_product_result,
    parse_aero_input,
)
from messenger.contracts import ProgressEvent
from messenger.profile_service import cleanup_product_result
from user_location_session import remember_location


class ParsedAeroRequest(NamedTuple):
    location_query: str
    lead_hour: int
    run: GfsRun | None
    diagram_type: str = AERO_DIAGRAM_TYPE


def parse_aero_request(
    raw_text: str,
    default_lead: int,
    default_diagram_type: str = AERO_DIAGRAM_TYPE,
) -> ParsedAeroRequest:
    """Compatibility wrapper around the messenger-neutral /aero parser."""

    parsed: ParsedAeroInput = parse_aero_input(raw_text, default_lead)
    return ParsedAeroRequest(
        parsed.location_query,
        parsed.lead_hour,
        parsed.run,
        AERO_DIAGRAM_TYPE,
    )


def _diagram_name(diagram_type: str = AERO_DIAGRAM_TYPE) -> str:
    return "Аэрологическая диаграмма"


def format_aero_file_caption(
    run: GfsRun,
    lead_hour: int,
    diagram_type: str = AERO_DIAGRAM_TYPE,
) -> str:
    return f"PNG · GFS · аэрологическая диаграмма · {run.date} {run.cycle}Z · +{lead_hour} ч"


def repeat_aero_command(point: GeoPoint, parsed: ParsedAeroRequest, run: GfsRun) -> str:
    return f"/aero {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} +{parsed.lead_hour}"


def format_repeat_aero_message(point: GeoPoint, parsed: ParsedAeroRequest, run: GfsRun) -> str:
    return f"📋 <code>{html.escape(repeat_aero_command(point, parsed, run))}</code>"


def _progress_text(point: GeoPoint, lead_hour: int, event: ProgressEvent) -> str:
    data = dict(event.data)
    header = (
        "⏳ Аэрологическая диаграмма GFS\n"
        f"📍 {point.label}\n"
        f"🕒 срок +{int(lead_hour)} ч\n"
    )
    if event.stage in {"check", "run"}:
        body = "1/5 Проверяю опубликованный цикл GFS…"
        if event.stage == "run" and data.get("run_date") and data.get("run_cycle"):
            body = f"1/5 GFS {data['run_date']} {data['run_cycle']}Z"
    elif event.stage == "grid":
        body = f"2/5 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage == "cache":
        body = "3/5 Данные найдены в кэше…"
    elif event.stage in {"download_start", "download", "download_done"}:
        total = data.get("total")
        downloaded = data.get("downloaded")
        if total and downloaded:
            pct = min(100.0, float(downloaded) * 100.0 / float(total))
            body = f"3/5 Загружаю данные: {pct:.0f}%"
        else:
            body = "3/5 Загружаю модельные данные…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/5 Читаю профиль и считаю диагностику…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/5 Формирую Skew-T и годограф…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


async def run_aero_product(
    message,
    point: GeoPoint,
    parsed: ParsedAeroRequest,
    gfs_semaphore,
) -> bool:
    """Render the common aero service for Telegram without meteorological duplication."""

    status = await message.reply_text(
        "⏳ Аэрологическая диаграмма GFS\n"
        f"📍 {point.label}\n"
        f"🕒 срок +{parsed.lead_hour} ч\n"
        "1/5 Проверяю опубликованный цикл GFS…"
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
            text = _progress_text(point, parsed.lead_hour, event)
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
                build_aero_product_result,
                point,
                parsed.lead_hour,
                parsed.run,
                progress_callback=progress,
            )
        stop.set()
        await reporter_task
        await status.edit_text(common_result.summary)

        for attachment in common_result.attachments:
            with attachment.path.open("rb") as file_obj:
                telegram_file = InputFile(file_obj, filename=attachment.filename)
                if attachment.kind == "image":
                    await message.reply_photo(photo=telegram_file, caption=attachment.caption)
                elif attachment.kind == "animation":
                    await message.reply_animation(animation=telegram_file, caption=attachment.caption)
                else:
                    await message.reply_document(document=telegram_file, caption=attachment.caption)

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


async def resolve_aero_request(
    message,
    raw: str,
    default_lead: int,
    gfs_semaphore,
    geocode_semaphore,
    default_diagram_type: str = AERO_DIAGRAM_TYPE,
    user_id: int = 0,
) -> bool:
    try:
        parsed = parse_aero_request(raw, default_lead=default_lead)
        async with geocode_semaphore:
            candidates = await asyncio.to_thread(search_location_candidates, parsed.location_query, 3)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return False

    if not candidates:
        await message.reply_text("Точка не найдена. Укажите город, координаты или отправьте геолокацию.")
        return False

    if len(candidates) > 1:
        labels = "\n".join(f"{index + 1}. {point.label}" for index, point in enumerate(candidates[:3]))
        await message.reply_text(
            "Найдено несколько точек. Уточните название или используйте координаты.\n\n"
            f"Варианты:\n{labels}"
        )
        return False

    remember_location(user_id, candidates[0])
    return await run_aero_product(message, candidates[0], parsed, gfs_semaphore)
