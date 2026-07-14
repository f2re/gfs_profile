from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import NamedTuple

from telegram.constants import ParseMode

from cloudgram_render import write_cloudgram_png
from cloudgram_product import CLOUDGRAM_DEFAULT_STEP, CLOUDGRAM_DEFAULT_TO, CloudgramData, build_cloudgram_data, cloudgram_leads
from geocode import GeocodeError, GeoPoint
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from product_progress import run_product_with_progress
from telegram_file_send import reply_png_file
from user_location_session import remember_location

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,2})\b", re.IGNORECASE)
MODE_RE = re.compile(r"\bmode=(?P<value>[\wа-яё-]+)\b", re.IGNORECASE)


class ParsedCloudgramRequest(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    mode: str = "pro"


def normalize_cloudgram_mode(value: str | None) -> str:
    raw = (value or "pro").strip().lower().replace("ё", "е")
    if raw in {"simple", "simp", "easy", "lite", "user", "простои", "простой", "упрощенно", "упрощенныи", "упрощенный"}:
        return "simple"
    if raw in {"pro", "prof", "professional", "meteo", "профи", "профессионально"}:
        return "pro"
    raise GfsProfileError("mode должен быть pro или simple")


def _pop_int(pattern: re.Pattern[str], text: str, default: int) -> tuple[int, str]:
    match = pattern.search(text)
    if not match:
        return default, text
    value = int(match.group("value"))
    return value, (text[: match.start()] + text[match.end() :]).strip()


def _pop_mode(text: str) -> tuple[str, str]:
    match = MODE_RE.search(text)
    if not match:
        return "pro", text
    mode = normalize_cloudgram_mode(match.group("value"))
    return mode, (text[: match.start()] + text[match.end() :]).strip()


def parse_cloudgram_request(raw_text: str) -> ParsedCloudgramRequest:
    text = raw_text.strip()
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    mode, text = _pop_mode(text)
    lead_from, text = _pop_int(FROM_RE, text, 0)
    lead_to, text = _pop_int(TO_RE, text, CLOUDGRAM_DEFAULT_TO)
    step, text = _pop_int(STEP_RE, text, CLOUDGRAM_DEFAULT_STEP)
    cloudgram_leads(lead_from, lead_to, step)
    if not text:
        raise ValueError("Не указана точка. Пример: /cloudgram Москва to=72 step=3 mode=simple")
    return ParsedCloudgramRequest(text, run, lead_from, lead_to, step, mode)


def _mode_title(mode: str) -> str:
    return "SIMPLE" if mode == "simple" else "PRO"


def _mode_description(mode: str) -> str:
    if mode == "simple":
        return "облака по ярусам, осадки/явления, гроза, видимость, опасность"
    return "облачность H/M/L, осадки, явления, видимость, ВНГО, грозовой риск, опасность"


def _hazard_label(value: int) -> str:
    return {
        0: "0/4 — спокойно",
        1: "1/4 — слабые явления",
        2: "2/4 — ограничения",
        3: "3/4 — опасно",
        4: "4/4 — гроза / очень опасно",
    }.get(max(0, min(int(value), 4)), f"{value}/4")


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
    return (
        f"/cloudgram {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} "
        f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode={parsed.mode}"
    )


def format_repeat_cloudgram_message(point: GeoPoint, parsed: ParsedCloudgramRequest, run: GfsRun) -> str:
    command = html.escape(repeat_cloudgram_command(point, parsed, run))
    return (
        "📋 Повторить этот расчёт:\n"
        f"<code>{command}</code>\n\n"
        "Нажмите на строку команды и скопируйте её целиком."
    )


async def run_cloudgram_product(message, point: GeoPoint, parsed: ParsedCloudgramRequest, gfs_semaphore) -> bool:
    status = await message.reply_text(
        f"⏳ Cloudgram {_mode_title(parsed.mode)}\n"
        f"📍 {point.label}\n"
        f"🕒 GFS +{parsed.lead_from}…+{parsed.lead_to} ч, шаг {parsed.step} ч\n"
        "1/6 выбираю опубликованный цикл GFS…"
    )
    png_path: Path | None = None
    selected_run: GfsRun | None = None
    success = False
    try:
        leads = cloudgram_leads(parsed.lead_from, parsed.lead_to, parsed.step)
        selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, max(leads))
        async with gfs_semaphore:
            header = (
                f"☁️ CLOUDGRAM {_mode_title(parsed.mode)}\n"
                f"GFS {selected_run.date} {selected_run.cycle}Z · UTC · +{leads[0]}…+{leads[-1]} ч · шаг {parsed.step} ч\n"
                f"{point.label}\n{point.lat:.4f}, {point.lon:.4f}"
            )

            def worker(progress_callback):
                data = build_cloudgram_data(
                    selected_run,
                    point.lat,
                    point.lon,
                    lead_from=parsed.lead_from,
                    lead_to=parsed.lead_to,
                    step=parsed.step,
                    progress_callback=progress_callback,
                )
                progress_callback({"stage": "plot_start", "message": "строю PNG"})
                path = write_cloudgram_png(data, mode=parsed.mode)
                progress_callback({"stage": "plot_done", "message": "PNG готов", "file": str(path)})
                return data, path

            data, png_path = await run_product_with_progress(status, header, worker)
        await status.edit_text(format_cloudgram_caption(data, parsed.mode))
        if png_path:
            await reply_png_file(message, png_path, caption=format_cloudgram_file_caption(data, parsed.mode), prefer_photo=len(leads) <= 12)
        await message.reply_text(format_repeat_cloudgram_message(point, parsed, selected_run), parse_mode=ParseMode.HTML)
        success = True
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)
    return success


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
