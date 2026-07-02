from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile
from telegram.constants import ParseMode

from composite_map import MAP_MAX_ANIMATION_FRAMES, MAP_RADIUS_KM, build_composite_map, build_composite_map_frames, write_composite_map_gif, write_composite_map_png
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead, validate_lead
from product_progress import run_product_with_progress

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,3})\b", re.IGNORECASE)
ANIM_RE = re.compile(r"\banim=(?P<value>1|0|yes|no|true|false|да|нет)\b", re.IGNORECASE)
RADIUS_RE = re.compile(r"\bradius=(?P<value>\d{1,3})\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:^|\s)(?:lead=|\+|f)(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?(?=\s|$)", re.IGNORECASE)


class ParsedMapRequest(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    animate: bool
    radius_km: float


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "yes", "true", "да"}


def _strip_match(text: str, match: re.Match[str] | None) -> str:
    if not match:
        return text
    return (text[: match.start()] + text[match.end() :]).strip()


def _lead_list(parsed: ParsedMapRequest) -> list[int]:
    if parsed.lead_to < parsed.lead_from:
        raise ValueError("to должен быть не меньше from")
    if parsed.step <= 0:
        raise ValueError("step должен быть положительным")
    leads = list(range(parsed.lead_from, parsed.lead_to + 1, parsed.step))
    if not leads or leads[-1] != parsed.lead_to:
        leads.append(parsed.lead_to)
    for lead in leads:
        validate_lead(lead)
    if parsed.animate and len(leads) > MAP_MAX_ANIMATION_FRAMES:
        raise ValueError(f"Слишком много кадров для Telegram GIF: {len(leads)}. Увеличьте step или уменьшите to. Максимум {MAP_MAX_ANIMATION_FRAMES}.")
    return leads


def parse_map_request(raw_text: str, default_lead: int = 24) -> ParsedMapRequest:
    text = raw_text.strip()
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = _strip_match(text, run_match)

    lead_from = 0
    lead_to = default_lead
    step = 3
    animate: bool | None = None
    radius_km = MAP_RADIUS_KM

    for regex, name in ((FROM_RE, "from"), (TO_RE, "to"), (STEP_RE, "step"), (RADIUS_RE, "radius")):
        match = regex.search(text)
        if not match:
            continue
        value = int(match.group("value"))
        if name == "from":
            lead_from = value
        elif name == "to":
            lead_to = value
        elif name == "step":
            step = value
        elif name == "radius":
            radius_km = float(value)
        text = _strip_match(text, match)

    anim_match = ANIM_RE.search(text)
    if anim_match:
        animate = _truthy(anim_match.group("value"))
        text = _strip_match(text, anim_match)

    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead_from = int(lead_match.group("lead"))
        lead_to = lead_from
        animate = False if animate is None else animate
        text = _strip_match(text, lead_match)

    if radius_km <= 0 or radius_km > 100:
        raise ValueError("Для Telegram-карты radius должен быть в диапазоне 1..100 км")
    if animate is None:
        animate = lead_to > lead_from
    if not text:
        raise ValueError("Не указана точка. Пример: /map Москва +24 или /map Москва from=0 to=24 step=3")
    parsed = ParsedMapRequest(text, run, lead_from, lead_to, step, animate, radius_km)
    _lead_list(parsed)
    return parsed


def format_map_file_caption(data: dict, *, animated: bool = False) -> str:
    run = data["run"]
    point = data["point"]
    missing = data.get("missing") or set()
    kind = "GIF" if animated else "PNG"
    lines = [
        f"{kind} · MAP · GFS {run.date} {run.cycle}Z · UTC",
        f"{point.label} · {point.lat:.4f}, {point.lon:.4f}",
        f"радиус {int(data['radius_km'])} км · модель GFS 0.25",
    ]
    if missing:
        lines.append("Нет полей: " + ", ".join(sorted(missing)))
    return "\n".join(lines)


def format_map_status(data: dict, *, animated: bool = False) -> str:
    run = data["run"]
    point = data["point"]
    lead = data["lead_hour"]
    lines = [
        "🗺️ Композитная карта GFS",
        f"📍 {point.label}",
        f"🕒 {run.date} {run.cycle}Z · +{lead} ч · UTC",
        "Слои: осадки, облачность, гроза, ветер AT500, явления, видимость.",
        "GFS 0.25: это модельная карта, не радар и не наблюдения.",
    ]
    if animated:
        lines[0] = "🗺️ Композитная анимация GFS"
    return "\n".join(lines)


def _repeat_command(point: GeoPoint, parsed: ParsedMapRequest, run: GfsRun) -> str:
    if parsed.lead_from == parsed.lead_to:
        time_part = f"+{parsed.lead_to}"
    else:
        time_part = f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} anim={1 if parsed.animate else 0}"
    radius_part = "" if int(parsed.radius_km) == int(MAP_RADIUS_KM) else f" radius={int(parsed.radius_km)}"
    return f"/map {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} {time_part}{radius_part}"


def format_repeat_map_message(point: GeoPoint, parsed: ParsedMapRequest, run: GfsRun) -> str:
    command = html.escape(_repeat_command(point, parsed, run))
    return "📋 Повторить карту:\n" f"<code>{command}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


async def run_map_product(message, point: GeoPoint, parsed: ParsedMapRequest, gfs_semaphore) -> None:
    leads = _lead_list(parsed)
    selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, max(leads))
    lead_text = f"+{leads[0]} ч" if len(leads) == 1 else f"+{leads[0]}…+{leads[-1]} ч, шаг {parsed.step} ч"
    status = await message.reply_text(
        "⏳ Композитная карта GFS\n"
        f"📍 {point.label}\n"
        f"🕒 {lead_text}\n"
        "1/7 выбираю опубликованный цикл GFS…"
    )
    out_path: Path | None = None
    first_data: dict | None = None
    try:
        async with gfs_semaphore:
            header = (
                "🗺️ MAP · композитная карта\n"
                f"GFS {selected_run.date} {selected_run.cycle}Z · UTC · {lead_text}\n"
                f"{point.label}\n{point.lat:.4f}, {point.lon:.4f}"
            )

            def worker(progress_callback):
                if parsed.animate and len(leads) > 1:
                    frames = build_composite_map_frames(selected_run, leads, point, radius_km=parsed.radius_km, progress_callback=progress_callback)
                    progress_callback({"stage": "map_animation_start", "message": "Собираю GIF для Telegram"})
                    path = write_composite_map_gif(frames, progress_callback=progress_callback)
                    return frames[0], path, True
                data = build_composite_map(selected_run, leads[-1], point, radius_km=parsed.radius_km, progress_callback=progress_callback)
                path = write_composite_map_png(data, progress_callback=progress_callback)
                return data, path, False

            first_data, out_path, animated = await run_product_with_progress(status, header, worker)
        await status.edit_text(format_map_status(first_data, animated=animated))
        if out_path:
            caption = format_map_file_caption(first_data, animated=animated)
            with out_path.open("rb") as file_obj:
                if animated:
                    await message.reply_animation(animation=InputFile(file_obj, filename=out_path.name), caption=caption)
                else:
                    await message.reply_photo(photo=InputFile(file_obj, filename=out_path.name), caption=caption)
        await message.reply_text(format_repeat_map_message(point, parsed, selected_run), parse_mode=ParseMode.HTML)
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if out_path:
            out_path.unlink(missing_ok=True)


async def resolve_map_request(message, raw: str, gfs_semaphore, geocode_semaphore, default_lead: int = 24) -> None:
    try:
        parsed = parse_map_request(raw, default_lead=default_lead)
        async with geocode_semaphore:
            candidates = await asyncio.to_thread(search_location_candidates, parsed.location_query, 3)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return

    if not candidates:
        await message.reply_text("Точка не найдена. Пришлите координаты, город или геолокацию Telegram.")
        return
    if len(candidates) > 1:
        labels = "\n".join(f"{i + 1}. {point.label}" for i, point in enumerate(candidates[:3]))
        await message.reply_text(
            "Найдено несколько точек. Уточните запрос или используйте координаты.\n\n"
            f"Пример:\n/map {candidates[0].label} from={parsed.lead_from} to={parsed.lead_to} step={parsed.step}\n\n"
            f"Варианты:\n{labels}"
        )
        return
    await run_map_product(message, candidates[0], parsed, gfs_semaphore)
