from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest

from composite_map import MAP_BASEMAP_DEFAULT, MAP_BASEMAPS, MAP_MAX_ANIMATION_FRAMES, MAP_MAX_PNG_SERIES_FRAMES, MAP_RADIUS_KM, build_composite_map, build_composite_map_frames, write_composite_map_gif, write_composite_map_png
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead, validate_lead
from map_animation import write_composite_map_mp4
from product_progress import run_product_with_progress
from user_location_session import remember_location

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,3})\b", re.IGNORECASE)
ANIM_RE = re.compile(r"\banim=(?P<value>1|0|yes|no|true|false|да|нет)\b", re.IGNORECASE)
MODE_RE = re.compile(r"\bmode=(?P<value>single|one|png|series|png-series|gif|anim|animation)\b", re.IGNORECASE)
BASEMAP_RE = re.compile(r"\b(?:basemap|base|подложка)=(?P<value>basic|water|places|roads|база|вода|города|дороги)\b", re.IGNORECASE)
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
    mode: str = "single"
    basemap: str = MAP_BASEMAP_DEFAULT


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "yes", "true", "да"}


def _normalize_mode(value: str) -> str:
    normalized = value.lower()
    if normalized in {"single", "one"}:
        return "single"
    if normalized == "png":
        return "png"
    if normalized in {"series", "png-series"}:
        return "series"
    if normalized in {"gif", "anim", "animation"}:
        return "gif"
    raise ValueError("mode должен быть single, series или gif")


def _normalize_basemap(value: str) -> str:
    aliases = {
        "база": "basic",
        "вода": "water",
        "города": "places",
        "дороги": "roads",
    }
    normalized = aliases.get(value.lower(), value.lower())
    if normalized not in MAP_BASEMAPS:
        raise ValueError("basemap должен быть basic, water, places или roads")
    return normalized


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
    if parsed.mode == "gif" and len(leads) > MAP_MAX_ANIMATION_FRAMES:
        raise ValueError(f"Слишком много кадров для Telegram-анимации: {len(leads)}. Увеличьте step или уменьшите to. Максимум {MAP_MAX_ANIMATION_FRAMES}.")
    if parsed.mode == "series" and len(leads) > MAP_MAX_PNG_SERIES_FRAMES:
        raise ValueError(f"Слишком много PNG-карт для серии: {len(leads)}. Увеличьте step или уменьшите to. Максимум {MAP_MAX_PNG_SERIES_FRAMES}.")
    return leads


def parse_map_request(raw_text: str, default_lead: int = 24) -> ParsedMapRequest:
    text = raw_text.strip()
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = _strip_match(text, run_match)

    lead_from = default_lead
    lead_to = default_lead
    step = 3
    animate: bool | None = None
    explicit_mode: str | None = None
    basemap = MAP_BASEMAP_DEFAULT
    radius_km = MAP_RADIUS_KM
    range_mode = False

    for regex, name in ((FROM_RE, "from"), (TO_RE, "to"), (STEP_RE, "step"), (RADIUS_RE, "radius")):
        match = regex.search(text)
        if not match:
            continue
        value = int(match.group("value"))
        if name == "from":
            range_mode = True
            lead_from = value
        elif name == "to":
            range_mode = True
            lead_to = value
        elif name == "step":
            range_mode = True
            step = value
        elif name == "radius":
            radius_km = float(value)
        text = _strip_match(text, match)

    anim_match = ANIM_RE.search(text)
    if anim_match:
        animate = _truthy(anim_match.group("value"))
        text = _strip_match(text, anim_match)

    mode_match = MODE_RE.search(text)
    if mode_match:
        explicit_mode = _normalize_mode(mode_match.group("value"))
        text = _strip_match(text, mode_match)

    basemap_match = BASEMAP_RE.search(text)
    if basemap_match:
        basemap = _normalize_basemap(basemap_match.group("value"))
        text = _strip_match(text, basemap_match)

    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead_from = int(lead_match.group("lead"))
        lead_to = lead_from
        animate = False if animate is None else animate
        range_mode = False
        text = _strip_match(text, lead_match)

    if range_mode:
        from_match = FROM_RE.search(raw_text)
        to_match = TO_RE.search(raw_text)
        if not from_match:
            lead_from = 0
        if not to_match:
            lead_to = default_lead

    if radius_km <= 0 or radius_km > 100:
        raise ValueError("Для Telegram-карты radius должен быть в диапазоне 1..100 км")
    if explicit_mode == "png":
        mode = "series" if range_mode else "single"
    elif explicit_mode is not None:
        mode = explicit_mode
    elif animate is True:
        mode = "gif"
    elif range_mode:
        mode = "series"
    else:
        mode = "single"
    animate = mode == "gif"
    if mode == "single":
        lead_to = lead_from
    if not text:
        raise ValueError("Не указана точка. Пример: /map Москва +24, /map Москва from=0 to=24 step=3 mode=series или mode=gif")
    parsed = ParsedMapRequest(text, run, lead_from, lead_to, step, animate, radius_km, mode, basemap)
    _lead_list(parsed)
    return parsed


def _mode_title(parsed_or_mode) -> str:
    mode = parsed_or_mode.mode if hasattr(parsed_or_mode, "mode") else str(parsed_or_mode)
    if mode == "gif":
        return "MP4-анимация"
    if mode == "series":
        return "Серия PNG"
    return "Одна карта"


def format_map_file_caption(data: dict, *, animated: bool = False, series: bool = False, animation_format: str = "MP4-анимация") -> str:
    run = data["run"]
    point = data["point"]
    missing = data.get("missing") or set()
    kind = animation_format if animated else "PNG-серия" if series else "PNG"
    lines = [
        f"{kind} · MAP · GFS {run.date} {run.cycle}Z · UTC",
        f"{point.label} · {point.lat:.4f}, {point.lon:.4f}",
        f"срок +{int(data['lead_hour'])} ч",
        f"радиус {int(data['radius_km'])} км · модель GFS 0.25",
    ]
    if missing:
        lines.append("Нет полей: " + ", ".join(sorted(missing)))
    if data.get("overlay_footer"):
        lines.append(str(data["overlay_footer"]))
    return "\n".join(lines)


def format_map_status(data: dict, *, animated: bool = False, series: bool = False, lead_count: int = 1) -> str:
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
        lines[0] = f"🗺️ Композитная MP4-анимация GFS ({lead_count} кадров)"
    elif series:
        lines[0] = f"🗺️ Серия PNG-карт GFS ({lead_count} кадров)"
    return "\n".join(lines)


def _repeat_command(point: GeoPoint, parsed: ParsedMapRequest, run: GfsRun) -> str:
    if parsed.lead_from == parsed.lead_to:
        time_part = f"+{parsed.lead_to}"
    elif parsed.mode == "gif":
        time_part = f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode=gif"
    else:
        time_part = f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode=series"
    radius_part = "" if int(parsed.radius_km) == int(MAP_RADIUS_KM) else f" radius={int(parsed.radius_km)}"
    basemap_part = "" if parsed.basemap == MAP_BASEMAP_DEFAULT else f" basemap={parsed.basemap}"
    return f"/map {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} {time_part}{radius_part}{basemap_part}"


def format_repeat_map_message(point: GeoPoint, parsed: ParsedMapRequest, run: GfsRun) -> str:
    command = html.escape(_repeat_command(point, parsed, run))
    return "📋 Повторить карту:\n" f"<code>{command}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


def _series_frame_caption(path: Path) -> str:
    match = re.search(r"_f(?P<lead>\d{3})_", path.name)
    if match:
        return f"MAP +{int(match.group('lead')):03d} ч"
    return f"MAP · {path.stem}"


async def send_png_series(message, paths: list[Path], caption: str) -> None:
    for start in range(0, len(paths), 10):
        batch = paths[start : start + 10]
        batch_caption = caption if start == 0 else None
        if len(batch) == 1:
            with batch[0].open("rb") as file_obj:
                await message.reply_photo(photo=InputFile(file_obj, filename=batch[0].name), caption=batch_caption)
            continue
        files = [path.open("rb") for path in batch]
        try:
            media = []
            for index, (path, file_obj) in enumerate(zip(batch, files)):
                item_caption = batch_caption if index == 0 else None
                media.append(InputMediaPhoto(media=InputFile(file_obj, filename=path.name), caption=item_caption))
            await message.reply_media_group(media=media)
        except Exception:
            for file_obj in files:
                file_obj.close()
            files = []
            for index, path in enumerate(batch):
                item_caption = batch_caption if index == 0 else _series_frame_caption(path)
                with path.open("rb") as file_obj:
                    await message.reply_photo(photo=InputFile(file_obj, filename=path.name), caption=item_caption)
        finally:
            for file_obj in files:
                file_obj.close()


async def send_map_animation(message, path: Path, caption: str) -> None:
    try:
        with path.open("rb") as file_obj:
            await message.reply_animation(animation=InputFile(file_obj, filename=path.name), caption=caption)
        return
    except BadRequest:
        if path.suffix.lower() == ".mp4":
            try:
                with path.open("rb") as file_obj:
                    await message.reply_video(video=InputFile(file_obj, filename=path.name), caption=caption, supports_streaming=True)
                return
            except BadRequest:
                pass
    with path.open("rb") as file_obj:
        await message.reply_document(document=InputFile(file_obj, filename=path.name), caption=caption)


def _animation_format(path: Path) -> str:
    return "MP4-анимация" if path.suffix.lower() == ".mp4" else "GIF"


async def run_map_product(message, point: GeoPoint, parsed: ParsedMapRequest, gfs_semaphore) -> None:
    leads = _lead_list(parsed)
    selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, max(leads))
    lead_text = f"+{leads[0]} ч" if parsed.mode == "single" else f"+{leads[0]}…+{leads[-1]} ч, шаг {parsed.step} ч"
    mode_text = _mode_title(parsed)
    status = await message.reply_text(
        f"⏳ {mode_text}: композитная карта GFS\n"
        f"📍 {point.label}\n"
        f"🕒 {lead_text}\n"
        "1/7 выбираю опубликованный цикл GFS…"
    )
    out_paths: list[Path] = []
    first_data: dict | None = None
    try:
        async with gfs_semaphore:
            header = (
                f"🗺️ MAP · {mode_text}\n"
                f"GFS {selected_run.date} {selected_run.cycle}Z · UTC · {lead_text}\n"
                f"{point.label}\n{point.lat:.4f}, {point.lon:.4f}"
            )

            def worker(progress_callback):
                if parsed.mode == "gif":
                    frames = build_composite_map_frames(selected_run, leads, point, radius_km=parsed.radius_km, basemap=parsed.basemap, progress_callback=progress_callback)
                    progress_callback({"stage": "map_animation_start", "message": "Собираю MP4-анимацию для Telegram"})
                    try:
                        path = write_composite_map_mp4(frames, progress_callback=progress_callback)
                    except GfsProfileError as exc:
                        if "ffmpeg" not in str(exc).lower():
                            raise
                        progress_callback({"stage": "map_animation_fallback", "message": "ffmpeg не найден, собираю GIF"})
                        path = write_composite_map_gif(frames, progress_callback=progress_callback)
                    return frames[0], [path], True, False
                if parsed.mode == "series":
                    frames = build_composite_map_frames(selected_run, leads, point, radius_km=parsed.radius_km, basemap=parsed.basemap, progress_callback=progress_callback)
                    paths: list[Path] = []
                    for index, frame in enumerate(frames, start=1):
                        progress_callback({"stage": "map_series_frame", "message": f"Строю PNG {index}/{len(frames)}", "index": index, "total": len(frames), "lead_hour": frame["lead_hour"]})
                        paths.append(write_composite_map_png(frame, progress_callback=progress_callback))
                    return frames[0], paths, False, True
                data = build_composite_map(selected_run, leads[0], point, radius_km=parsed.radius_km, basemap=parsed.basemap, progress_callback=progress_callback)
                path = write_composite_map_png(data, progress_callback=progress_callback)
                return data, [path], False, False

            first_data, out_paths, animated, series = await run_product_with_progress(status, header, worker)
        await status.edit_text(format_map_status(first_data, animated=animated, series=series, lead_count=len(leads)))
        if out_paths:
            if animated:
                out_path = out_paths[0]
                caption = format_map_file_caption(first_data, animated=True, animation_format=_animation_format(out_path))
                await send_map_animation(message, out_path, caption)
            elif series:
                caption = format_map_file_caption(first_data, series=True)
                await send_png_series(message, out_paths, caption)
            else:
                out_path = out_paths[0]
                caption = format_map_file_caption(first_data)
                with out_path.open("rb") as file_obj:
                    await message.reply_photo(photo=InputFile(file_obj, filename=out_path.name), caption=caption)
        await message.reply_text(format_repeat_map_message(point, parsed, selected_run), parse_mode=ParseMode.HTML)
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        for out_path in out_paths:
            out_path.unlink(missing_ok=True)


async def resolve_map_request(message, raw: str, gfs_semaphore, geocode_semaphore, default_lead: int = 24, user_id: int = 0) -> None:
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
            f"Пример:\n/map {candidates[0].label} from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode={parsed.mode}\n\n"
            f"Варианты:\n{labels}"
        )
        return
    remember_location(user_id, candidates[0])
    await run_map_product(message, candidates[0], parsed, gfs_semaphore)
