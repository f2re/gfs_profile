from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import NamedTuple

from telegram.constants import ParseMode

from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from product_progress import run_product_with_progress
from telegram_file_send import reply_png_file
from user_location_session import remember_location
from windgram_plot import write_windgram_png
from windgram_product import WindgramData, build_windgram_data, normalize_windgram_param, windgram_leads

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,2})\b", re.IGNORECASE)
TOP_RE = re.compile(r"\btop=(?P<value>\d{3,4})\b", re.IGNORECASE)
PARAM_RE = re.compile(r"\b(?:param|field|параметр)=(?P<value>wind|ветер|v|speed|temp|t|temperature|температура|rh|humidity|влажность)\b", re.IGNORECASE)

PARAM_NAMES = {"wind": "ветер", "temp": "температура", "rh": "влажность"}
PARAM_CAPTIONS = {
    "wind": "цвет/число = скорость ветра, стрелка = направление",
    "temp": "цвет/число = температура, стрелка = направление ветра",
    "rh": "цвет/число = влажность, стрелка = направление ветра",
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
    return (
        f"/windgram {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} "
        f"from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} top={parsed.top_hpa} param={parsed.param}"
    )


def format_repeat_windgram_message(point: GeoPoint, parsed: ParsedWindgramRequest, run: GfsRun) -> str:
    command = html.escape(repeat_windgram_command(point, parsed, run))
    return "📋 Повторить этот расчёт:\n" f"<code>{command}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


async def run_windgram_product(message, point: GeoPoint, parsed: ParsedWindgramRequest, gfs_semaphore) -> None:
    leads = windgram_leads(lead_from=parsed.lead_from, lead_to=parsed.lead_to, step=parsed.step)
    selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, max(leads))
    status = await message.reply_text(
        f"⏳ Windgram · {PARAM_NAMES.get(parsed.param, parsed.param)}\n"
        f"📍 {point.label}\n"
        f"🕒 GFS +{leads[0]}…+{leads[-1]} ч, шаг {parsed.step} ч\n"
        "1/6 выбираю опубликованный цикл GFS…"
    )
    png_path: Path | None = None
    try:
        async with gfs_semaphore:
            header = (
                f"🟦 WINDGRAM · {PARAM_NAMES.get(parsed.param, parsed.param)}\n"
                f"GFS {selected_run.date} {selected_run.cycle}Z · UTC · +{leads[0]}…+{leads[-1]} ч · шаг {parsed.step} ч\n"
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
                progress_callback({"stage": "plot_start", "message": "строю PNG"})
                path = write_windgram_png(data, param=parsed.param)
                progress_callback({"stage": "plot_done", "message": "PNG готов", "file": str(path)})
                return data, path

            data, png_path = await run_product_with_progress(status, header, worker)
        await status.edit_text(format_windgram_caption(data))
        if png_path:
            await reply_png_file(message, png_path, caption=format_windgram_file_caption(data), prefer_photo=len(leads) <= 12)
        await message.reply_text(format_repeat_windgram_message(point, parsed, selected_run), parse_mode=ParseMode.HTML)
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)


async def resolve_windgram_request(message, raw: str, gfs_semaphore, geocode_semaphore, user_id: int = 0) -> None:
    try:
        parsed = parse_windgram_request(raw)
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
            f"Пример:\n/windgram {candidates[0].label} to={parsed.lead_to} step={parsed.step} param={parsed.param}\n\n"
            f"Варианты:\n{labels}"
        )
        return

    remember_location(user_id, candidates[0])
    await run_windgram_product(message, candidates[0], parsed, gfs_semaphore)
