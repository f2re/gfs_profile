from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from threading import Lock
from typing import NamedTuple

from telegram import InputFile, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest

from composite_map import MAP_BASEMAP_DEFAULT, MAP_RADIUS_KM
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from messenger.contracts import ProgressEvent
from messenger.map_service import (
    ParsedMapInput,
    build_map_product_result,
    map_leads,
    parse_map_input,
)
from messenger.profile_service import cleanup_product_result
from user_location_session import remember_location

ANIM_RE = re.compile(r"\banim=(?P<value>1|0|yes|no|true|false|да|нет)\b", re.IGNORECASE)


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


def parse_map_request(raw_text: str, default_lead: int = 24) -> ParsedMapRequest:
    """Compatibility facade over the common map parser.

    Legacy ``anim=1`` remains accepted. The common parser owns all current
    range/mode/basemap/radius validation.
    """
    text = str(raw_text or "").strip()
    anim_match = ANIM_RE.search(text)
    if anim_match:
        animate = _truthy(anim_match.group("value"))
        replacement = "mode=gif" if animate else ""
        text = (text[: anim_match.start()] + replacement + text[anim_match.end() :]).strip()
    try:
        parsed: ParsedMapInput = parse_map_input(text)
    except GfsProfileError as exc:
        # Historical Telegram parser exposed user input validation as ValueError.
        raise ValueError(str(exc)) from exc
    return ParsedMapRequest(
        parsed.location_query,
        parsed.run,
        parsed.lead_from,
        parsed.lead_to,
        parsed.step,
        parsed.mode == "gif",
        parsed.radius_km,
        parsed.mode,
        parsed.basemap,
    )


def _lead_list(parsed: ParsedMapRequest) -> list[int]:
    try:
        return map_leads(parsed.lead_from, parsed.lead_to, parsed.step, parsed.mode)
    except GfsProfileError as exc:
        raise ValueError(str(exc)) from exc


def _mode_title(parsed_or_mode) -> str:
    mode = parsed_or_mode.mode if hasattr(parsed_or_mode, "mode") else str(parsed_or_mode)
    return {"gif": "MP4-анимация", "series": "Серия PNG", "single": "Одна карта"}.get(mode, "Одна карта")


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
    return "\n".join(lines)


def format_map_status(data: dict, *, animated: bool = False, series: bool = False, lead_count: int = 1) -> str:
    run = data["run"]
    point = data["point"]
    lead = data["lead_hour"]
    title = "🗺️ Композитная карта GFS"
    if animated:
        title = f"🗺️ Композитная MP4-анимация GFS ({lead_count} кадров)"
    elif series:
        title = f"🗺️ Серия PNG-карт GFS ({lead_count} кадров)"
    return (
        f"{title}\n📍 {point.label}\n🕒 {run.date} {run.cycle}Z · +{lead} ч · UTC\n"
        "Слои: осадки, облачность, гроза, ветер 500 гПа, явления, видимость.\n"
        "GFS 0.25: модельная карта, не радар и не наблюдения."
    )


def _repeat_command(point: GeoPoint, parsed: ParsedMapRequest, run: GfsRun) -> str:
    if parsed.lead_from == parsed.lead_to:
        time_part = f"+{parsed.lead_to}"
    else:
        time_part = f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode={parsed.mode}"
    radius_part = "" if int(parsed.radius_km) == int(MAP_RADIUS_KM) else f" radius={int(parsed.radius_km)}"
    basemap_part = "" if parsed.basemap == MAP_BASEMAP_DEFAULT else f" basemap={parsed.basemap}"
    return f"/map {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} {time_part}{radius_part}{basemap_part}"


def format_repeat_map_message(point: GeoPoint, parsed: ParsedMapRequest, run: GfsRun) -> str:
    return "📋 Повторить карту:\n" f"<code>{html.escape(_repeat_command(point, parsed, run))}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


def _series_frame_caption(path: Path) -> str:
    match = re.search(r"_f(?P<lead>\d{3})_", path.name)
    return f"MAP +{int(match.group('lead')):03d} ч" if match else f"MAP · {path.stem}"


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
            media = [InputMediaPhoto(media=InputFile(file_obj, filename=path.name), caption=batch_caption if index == 0 else None) for index, (path, file_obj) in enumerate(zip(batch, files))]
            await message.reply_media_group(media=media)
        except Exception:
            for file_obj in files:
                file_obj.close()
            files = []
            for index, path in enumerate(batch):
                with path.open("rb") as file_obj:
                    await message.reply_photo(photo=InputFile(file_obj, filename=path.name), caption=batch_caption if index == 0 else _series_frame_caption(path))
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


def _progress_text(point: GeoPoint, parsed: ParsedMapRequest, event: ProgressEvent) -> str:
    data = dict(event.data)
    period = f"+{parsed.lead_from} ч" if parsed.mode == "single" else f"+{parsed.lead_from}…+{parsed.lead_to} ч · шаг {parsed.step} ч"
    header = f"⏳ {_mode_title(parsed)} · карта GFS\n📍 {point.label}\n{period}\n"
    if event.stage in {"check", "run"}:
        body = "1/6 Проверяю опубликованный цикл GFS…"
        if event.stage == "run" and data.get("run_date"):
            body = f"1/6 GFS {data['run_date']} {data.get('run_cycle')}Z"
    elif event.stage in {"map_step", "download_start", "download", "download_done", "cache"}:
        current = data.get("index") or event.current
        total = data.get("total") or event.total
        lead = data.get("lead_hour")
        body = f"2/6 Загружаю поля {current}/{total} · +{lead} ч" if current and total and lead is not None else "2/6 Загружаю модельные поля…"
    elif event.stage in {"parse_start", "parse_done", "map_done"}:
        body = "3/6 Считаю слои карты…"
    elif event.stage.startswith("plot") or event.stage.startswith("map_animation"):
        body = event.message or "4/6 Формирую файл…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


async def run_map_product(message, point: GeoPoint, parsed: ParsedMapRequest, gfs_semaphore) -> bool:
    """Telegram renderer over the common map service."""
    status = await message.reply_text(_progress_text(point, parsed, ProgressEvent("check", "")))
    state = {"event": ProgressEvent("check", "")}
    lock = Lock()
    stop = asyncio.Event()
    last = ""
    result = None

    def progress(event: ProgressEvent) -> None:
        with lock:
            state["event"] = event

    async def reporter() -> None:
        nonlocal last
        while not stop.is_set():
            with lock:
                event = state["event"]
            text = _progress_text(point, parsed, event)
            if text != last:
                try:
                    await status.edit_text(text)
                    last = text
                except Exception:
                    pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(reporter())
    try:
        async with gfs_semaphore:
            result = await asyncio.to_thread(
                build_map_product_result,
                point,
                parsed.lead_from,
                parsed.lead_to,
                parsed.step,
                parsed.mode,
                parsed.radius_km,
                parsed.basemap,
                parsed.run,
                progress_callback=progress,
                run_selector=latest_available_run_for_lead,
            )
        stop.set()
        await task
        await status.edit_text(result.summary)
        image_paths: list[Path] = []
        for attachment in result.attachments:
            if attachment.kind == "animation":
                await send_map_animation(message, attachment.path, attachment.caption)
            elif attachment.kind == "image":
                image_paths.append(attachment.path)
            else:
                with attachment.path.open("rb") as file_obj:
                    await message.reply_document(document=InputFile(file_obj, filename=attachment.filename), caption=attachment.caption)
        if image_paths:
            if len(image_paths) > 1:
                await send_png_series(message, image_paths, result.attachments[0].caption)
            else:
                with image_paths[0].open("rb") as file_obj:
                    await message.reply_photo(photo=InputFile(file_obj, filename=image_paths[0].name), caption=result.attachments[0].caption)
        if result.repeat_command:
            await message.reply_text(f"📋 <code>{html.escape(result.repeat_command)}</code>", parse_mode=ParseMode.HTML)
        return True
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        stop.set(); await task; await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        stop.set(); await task; await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        stop.set()
        if not task.done():
            await task
        if result is not None:
            cleanup_product_result(result)
    return False


async def resolve_map_request(message, raw: str, gfs_semaphore, geocode_semaphore, default_lead: int = 24, user_id: int = 0) -> bool:
    try:
        parsed = parse_map_request(raw, default_lead=default_lead)
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
        await message.reply_text("Найдено несколько точек. Уточните запрос или используйте координаты.\n\n" f"Варианты:\n{labels}")
        return False
    remember_location(user_id, candidates[0])
    return await run_map_product(message, candidates[0], parsed, gfs_semaphore)
