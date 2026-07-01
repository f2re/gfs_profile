from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile

from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from product_progress import run_product_with_progress
from windgram_plot import write_windgram_png
from windgram_product import WindgramData, build_windgram_data, normalize_windgram_param, windgram_leads

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,2})\b", re.IGNORECASE)
TOP_RE = re.compile(r"\btop=(?P<value>\d{3,4})\b", re.IGNORECASE)
PARAM_RE = re.compile(r"\b(?:param|field|параметр)=(?P<value>wind|ветер|v|speed|temp|t|temperature|температура|rh|humidity|влажность)\b", re.IGNORECASE)

PARAM_CAPTIONS = {
    "wind": "Цвет и число — скорость ветра, стрелка — направление переноса.",
    "temp": "Цвет и число — температура, стрелка — направление переноса.",
    "rh": "Цвет и число — относительная влажность, стрелка — направление переноса.",
}


class ParsedWindgramRequest(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    top_hpa: int
    param: str


def _pop_int(pattern: re.Pattern[str], text: str, default: int) -> tuple[int, str]:
    match = pattern.search(text)
    if not match:
        return default, text
    value = int(match.group("value"))
    return value, (text[: match.start()] + text[match.end() :]).strip()


def _pop_param(text: str) -> tuple[str, str]:
    match = PARAM_RE.search(text)
    if not match:
        return "wind", text
    value = normalize_windgram_param(match.group("value"))
    return value, (text[: match.start()] + text[match.end() :]).strip()


def parse_windgram_request(raw_text: str) -> ParsedWindgramRequest:
    text = raw_text.strip()
    run: GfsRun | None = None

    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    param, text = _pop_param(text)
    lead_from, text = _pop_int(FROM_RE, text, 0)
    lead_to, text = _pop_int(TO_RE, text, 120)
    step, text = _pop_int(STEP_RE, text, 6)
    top_hpa, text = _pop_int(TOP_RE, text, 500)

    if lead_to > 384:
        raise GfsProfileError("to для windgram не может быть больше +384 ч")
    if top_hpa < 500:
        raise GfsProfileError("top ниже 500 гПа пока не поддерживается")
    windgram_leads(lead_from=lead_from, lead_to=lead_to, step=step)
    if not text:
        raise ValueError("Не указана точка. Пример: /windgram Москва to=120 param=temp")

    return ParsedWindgramRequest(text, run, lead_from, lead_to, step, top_hpa, param)


def format_windgram_caption(data: WindgramData) -> str:
    return (
        f"🟦 GFS 0.25 windgram · {data.param}\n"
        f"{data.run.date} {data.run.cycle}Z | +{data.leads[0]}…+{data.leads[-1]} ч | шаг {data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0} ч\n"
        f"⊞ {data.grid_lat:.3f},{data.grid_lon:.3f}\n"
        f"{PARAM_CAPTIONS.get(data.param, PARAM_CAPTIONS['wind'])}"
    )


def repeat_windgram_command(point: GeoPoint, parsed: ParsedWindgramRequest, run: GfsRun) -> str:
    return (
        f"/windgram {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} "
        f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} top={parsed.top_hpa} param={parsed.param}"
    )


async def run_windgram_product(message, point: GeoPoint, parsed: ParsedWindgramRequest, gfs_semaphore) -> None:
    leads = windgram_leads(lead_from=parsed.lead_from, lead_to=parsed.lead_to, step=parsed.step)
    selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, max(leads))
    status = await message.reply_text("0/6 Готовлю windgram: выбираю единый запуск GFS…")
    png_path: Path | None = None
    try:
        async with gfs_semaphore:
            header = (
                f"WINDGRAM {parsed.param.upper()} | GFS {selected_run.date} {selected_run.cycle}Z | +{leads[0]}…+{leads[-1]} ч\n"
                f"{point.label}\n{point.lat:.4f}, {point.lon:.4f}"
            )

            def worker(progress_callback):
                data = build_windgram_data(
                    selected_run,
                    point.lat,
                    point.lon,
                    lead_from=parsed.lead_from,
                    lead_to=parsed.lead_to,
                    step=parsed.step,
                    top_hpa=parsed.top_hpa,
                    param=parsed.param,
                    progress_callback=progress_callback,
                )
                progress_callback({"stage": "plot_start", "message": "Строю windgram"})
                path = write_windgram_png(data, param=parsed.param)
                progress_callback({"stage": "plot_done", "message": "Windgram готов", "file": str(path)})
                return data, path

            data, png_path = await run_product_with_progress(status, header, worker)
        await status.edit_text(format_windgram_caption(data))
        if png_path:
            with png_path.open("rb") as file_obj:
                if len(leads) > 25:
                    await message.reply_document(document=InputFile(file_obj, filename=png_path.name), caption=f"Windgram GFS: {parsed.param}")
                else:
                    await message.reply_photo(photo=InputFile(file_obj, filename=png_path.name), caption=f"Windgram GFS: {parsed.param}")
        await message.reply_text("Команда для повтора:\n" + repeat_windgram_command(point, parsed, selected_run))
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)


async def resolve_windgram_request(message, raw: str, gfs_semaphore, geocode_semaphore) -> None:
    try:
        parsed = parse_windgram_request(raw)
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
            "Найдено несколько точек. Для windgram уточните запрос текстом, например:\n"
            f"/windgram {candidates[0].label} to={parsed.lead_to} param={parsed.param}\n\n"
            f"Варианты:\n{labels}"
        )
        return

    await run_windgram_product(message, candidates[0], parsed, gfs_semaphore)
