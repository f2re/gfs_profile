from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile

from cloudgram_plot import write_cloudgram_png
from cloudgram_product import CLOUDGRAM_DEFAULT_STEP, CLOUDGRAM_DEFAULT_TO, CloudgramData, build_cloudgram_data, cloudgram_leads
from geocode import GeocodeError, GeoPoint
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from product_progress import run_product_with_progress

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


def format_cloudgram_caption(data: CloudgramData, mode: str = "pro") -> str:
    missing = f"\nНет части полей: {', '.join(data.missing_fields)}" if data.missing_fields else ""
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    label = "упрощённый" if mode == "simple" else "профессиональный"
    details = (
        "Облака, осадки, гроза, явления, видимость и общий уровень опасности."
        if mode == "simple"
        else "Облачность %, осадки мм/шаг, явления, видимость, ВНГО, грозовой риск 0–3 и опасность 0–4."
    )
    return (
        f"☁️ GFS 0.25 cloudgram · {label}\n"
        f"{data.run.date} {data.run.cycle}Z | +{data.leads[0]}…+{data.leads[-1]} ч | шаг {data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0} ч\n"
        f"⊞ {data.grid_lat:.3f},{data.grid_lon:.3f}\n"
        f"{details} Макс. опасность: {max_hazard}."
        f"{missing}"
    )


def repeat_cloudgram_command(point: GeoPoint, parsed: ParsedCloudgramRequest, run: GfsRun) -> str:
    return (
        f"/cloudgram {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} "
        f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode={parsed.mode}"
    )


async def run_cloudgram_product(message, point: GeoPoint, parsed: ParsedCloudgramRequest, gfs_semaphore) -> None:
    leads = cloudgram_leads(parsed.lead_from, parsed.lead_to, parsed.step)
    selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, max(leads))
    status = await message.reply_text("0/6 Готовлю cloudgram: выбираю единый запуск GFS…")
    png_path: Path | None = None
    try:
        async with gfs_semaphore:
            header = (
                f"CLOUDGRAM {parsed.mode.upper()} | GFS {selected_run.date} {selected_run.cycle}Z | +{leads[0]}…+{leads[-1]} ч\n"
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
                progress_callback({"stage": "plot_start", "message": "Строю cloudgram"})
                path = write_cloudgram_png(data, mode=parsed.mode)
                progress_callback({"stage": "plot_done", "message": "Cloudgram готов", "file": str(path)})
                return data, path

            data, png_path = await run_product_with_progress(status, header, worker)
        await status.edit_text(format_cloudgram_caption(data, parsed.mode))
        if png_path:
            with png_path.open("rb") as file_obj:
                if len(leads) > 25:
                    await message.reply_document(document=InputFile(file_obj, filename=png_path.name), caption=f"Cloudgram GFS · {parsed.mode}")
                else:
                    await message.reply_photo(photo=InputFile(file_obj, filename=png_path.name), caption=f"Cloudgram GFS · {parsed.mode}")
        await message.reply_text("Команда для повтора:\n" + repeat_cloudgram_command(point, parsed, selected_run))
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)


async def resolve_cloudgram_request(message, raw: str, gfs_semaphore, geocode_semaphore) -> None:
    try:
        parsed = parse_cloudgram_request(raw)
        async with geocode_semaphore:
            candidates = await asyncio.to_thread(search_location_candidates, parsed.location_query, 3)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return

    if not candidates:
        await message.reply_text("Точка не найдена. Пришлите координаты или геолокацию Telegram.")
        return
    if len(candidates) > 1:
        labels = "\n".join(f"{i + 1}. {point.label}" for i, point in enumerate(candidates[:3]))
        await message.reply_text(
            "Найдено несколько точек. Для cloudgram уточните запрос текстом, например:\n"
            f"/cloudgram {candidates[0].label} to={parsed.lead_to} step={parsed.step} mode={parsed.mode}\n\n"
            f"Варианты:\n{labels}"
        )
        return

    await run_cloudgram_product(message, candidates[0], parsed, gfs_semaphore)
