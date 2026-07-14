from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile
from telegram.constants import ParseMode

from aero_product import build_aero_product, format_aero_caption
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead, validate_lead
from product_progress import run_product_with_progress
from user_location_session import remember_location

AERO_TYPE_RE = re.compile(r"\btype=(?P<type>stuve|emagram|skewt)\b", re.IGNORECASE)
RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:^|\s)(?:lead=|\+|f)?(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?\s*$", re.IGNORECASE)
AERO_DIAGRAM_TYPE = "skewt"


class ParsedAeroRequest(NamedTuple):
    location_query: str
    lead_hour: int
    run: GfsRun | None
    diagram_type: str = AERO_DIAGRAM_TYPE


def parse_aero_request(raw_text: str, default_lead: int, default_diagram_type: str = AERO_DIAGRAM_TYPE) -> ParsedAeroRequest:
    """Parse /aero. Legacy type= is removed from the input and ignored."""

    text = raw_text.strip()
    type_match = AERO_TYPE_RE.search(text)
    if type_match:
        text = (text[: type_match.start()] + text[type_match.end() :]).strip()

    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    lead_hour = int(default_lead)
    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead_hour = int(lead_match.group("lead"))
        text = text[: lead_match.start()].strip()

    validate_lead(lead_hour)
    if not text:
        raise ValueError("Не указана точка. Пример: /aero Москва +24")
    return ParsedAeroRequest(text, lead_hour, run, AERO_DIAGRAM_TYPE)


def _diagram_name(diagram_type: str = AERO_DIAGRAM_TYPE) -> str:
    return "Аэрологическая диаграмма"


def format_aero_file_caption(run: GfsRun, lead_hour: int, diagram_type: str = AERO_DIAGRAM_TYPE) -> str:
    return f"PNG · GFS · аэрологическая диаграмма · {run.date} {run.cycle}Z · +{lead_hour} ч"


def repeat_aero_command(point: GeoPoint, parsed: ParsedAeroRequest, run: GfsRun) -> str:
    return f"/aero {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} +{parsed.lead_hour}"


def format_repeat_aero_message(point: GeoPoint, parsed: ParsedAeroRequest, run: GfsRun) -> str:
    return f"📋 <code>{html.escape(repeat_aero_command(point, parsed, run))}</code>"


async def run_aero_product(message, point: GeoPoint, parsed: ParsedAeroRequest, gfs_semaphore) -> None:
    status = await message.reply_text(
        "⏳ Аэрологическая диаграмма\n"
        f"📍 {point.label}\n"
        f"🕒 срок +{parsed.lead_hour} ч\n"
        "Выбираю цикл GFS…"
    )
    png_path: Path | None = None
    try:
        async with gfs_semaphore:
            selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, parsed.lead_hour)
            header = (
                "🧾 Аэрологическая диаграмма\n"
                f"GFS {selected_run.date} {selected_run.cycle}Z · +{parsed.lead_hour} ч\n"
                f"{point.label}"
            )

            def worker(progress_callback):
                return build_aero_product(
                    selected_run,
                    parsed.lead_hour,
                    point.lat,
                    point.lon,
                    AERO_DIAGRAM_TYPE,
                    progress_callback=progress_callback,
                )

            result, png_path = await run_product_with_progress(status, header, worker)
        await status.edit_text(format_aero_caption(result))
        if png_path:
            with png_path.open("rb") as file_obj:
                await message.reply_photo(
                    photo=InputFile(file_obj, filename=png_path.name),
                    caption=format_aero_file_caption(selected_run, parsed.lead_hour),
                )
        await message.reply_text(format_repeat_aero_message(point, parsed, selected_run), parse_mode=ParseMode.HTML)
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)


async def resolve_aero_request(
    message,
    raw: str,
    default_lead: int,
    gfs_semaphore,
    geocode_semaphore,
    default_diagram_type: str = AERO_DIAGRAM_TYPE,
    user_id: int = 0,
) -> None:
    try:
        parsed = parse_aero_request(raw, default_lead=default_lead)
        async with geocode_semaphore:
            candidates = await asyncio.to_thread(search_location_candidates, parsed.location_query, 3)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return

    if not candidates:
        await message.reply_text("Точка не найдена. Укажите город, координаты или отправьте геолокацию.")
        return

    if len(candidates) > 1:
        labels = "\n".join(f"{index + 1}. {point.label}" for index, point in enumerate(candidates[:3]))
        await message.reply_text(
            "Найдено несколько точек. Уточните название или используйте координаты.\n\n"
            f"Варианты:\n{labels}"
        )
        return

    remember_location(user_id, candidates[0])
    await run_aero_product(message, candidates[0], parsed, gfs_semaphore)
