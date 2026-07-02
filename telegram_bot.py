from __future__ import annotations

import asyncio
import html
import os
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from formatters import format_profile_summary, write_profile_csv
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import CACHE_DIR, GfsProfileError, GfsRun, latest_available_run, latest_available_run_for_lead, validate_lead
from profile_plot import write_profile_png
from telegram_aero import ParsedAeroRequest, resolve_aero_request, run_aero_product
from telegram_cloudgram import ParsedCloudgramRequest, resolve_cloudgram_request, run_cloudgram_product
from telegram_map import ParsedMapRequest, resolve_map_request, run_map_product
from telegram_product_wizard import (
    PRODUCT_WIZARD_KEY,
    params_keyboard,
    params_text,
    place_keyboard as wizard_place_keyboard,
    point_prompt_text,
    set_point as wizard_set_point,
    start_aero_wizard_state,
    start_cloudgram_wizard_state,
    start_map_wizard_state,
    start_windgram_wizard_state,
)
from telegram_progress import build_profile_with_progress
from telegram_ui import lead_keyboard, lead_page_text, location_keyboard, place_keyboard
from telegram_windgram import ParsedWindgramRequest, resolve_windgram_request, run_windgram_product

DEFAULT_LEAD = int(os.getenv("DEFAULT_LEAD", "24"))
MAX_CONCURRENT_GFS = int(os.getenv("MAX_CONCURRENT_GFS", "2"))
MAX_CONCURRENT_GEOCODE = int(os.getenv("MAX_CONCURRENT_GEOCODE", "2"))
GFS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_GFS)
GEOCODE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_GEOCODE)
RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:^|\s)(?:lead=|\+|f)?(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?\s*$", re.IGNORECASE)


class ParsedRequest(NamedTuple):
    location_query: str
    lead_hour: int
    run: GfsRun | None
    lead_from_user: bool


def parse_request(raw_text: str) -> ParsedRequest:
    text = raw_text.strip()
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    lead_hour = DEFAULT_LEAD
    lead_from_user = False
    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead_hour = int(lead_match.group("lead"))
        lead_from_user = True
        text = text[: lead_match.start()].strip()

    validate_lead(lead_hour)
    if not text:
        raise ValueError("Не указана точка. Пример: /profile Москва +24")
    return ParsedRequest(text, lead_hour, run, lead_from_user)


def _pack_point(point: GeoPoint) -> dict[str, object]:
    return {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source}


def _unpack_point(payload: dict[str, object]) -> GeoPoint:
    return GeoPoint(float(payload["lat"]), float(payload["lon"]), str(payload["label"]), str(payload.get("source", "manual")))


def _pack_run(run: GfsRun | None) -> dict[str, str] | None:
    return {"date": run.date, "cycle": run.cycle} if run else None


def _unpack_run(payload: dict[str, str] | None) -> GfsRun | None:
    return GfsRun(payload["date"], payload["cycle"]) if payload else None


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_profile", None)
    context.user_data.pop("pending_candidates", None)
    context.user_data.pop(PRODUCT_WIZARD_KEY, None)


def _set_pending_point(context: ContextTypes.DEFAULT_TYPE, point: GeoPoint, run: GfsRun | None = None) -> None:
    context.user_data["pending_profile"] = {"point": _pack_point(point), "run": _pack_run(run)}


def _point_brief(point: GeoPoint) -> str:
    return f"{point.label}\n{point.lat:.4f}, {point.lon:.4f}"


def _wizard_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, object] | None:
    state = context.user_data.get(PRODUCT_WIZARD_KEY)
    return state if isinstance(state, dict) else None


def _profile_command(point: GeoPoint, lead_hour: int, run: GfsRun) -> str:
    return f"/profile {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} +{lead_hour}"


def _profile_repeat_message(point: GeoPoint, lead_hour: int, run: GfsRun) -> str:
    command = html.escape(_profile_command(point, lead_hour, run))
    return "📋 Повторить профиль:\n" f"<code>{command}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


async def _start_product_wizard(message, context: ContextTypes.DEFAULT_TYPE, state: dict[str, object]) -> None:
    _clear_pending(context)
    context.user_data[PRODUCT_WIZARD_KEY] = state
    await message.reply_text(point_prompt_text(state), reply_markup=location_keyboard())


async def _show_wizard_params(message, context: ContextTypes.DEFAULT_TYPE, state: dict[str, object]) -> None:
    context.user_data[PRODUCT_WIZARD_KEY] = state
    await message.reply_text(params_text(state), reply_markup=params_keyboard(state))


async def _resolve_wizard_point(message, context: ContextTypes.DEFAULT_TYPE, raw: str) -> bool:
    state = _wizard_state(context)
    if not state or state.get("step") != "await_point":
        return False
    try:
        async with GEOCODE_SEMAPHORE:
            candidates = await asyncio.to_thread(search_location_candidates, raw, 5)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return True

    if not candidates:
        await message.reply_text("Точка не найдена. Отправьте координаты, город или геолокацию Telegram.")
        return True

    if len(candidates) > 1:
        state["candidates"] = [_pack_point(point) for point in candidates[:5]]
        state["step"] = "choose_place"
        context.user_data[PRODUCT_WIZARD_KEY] = state
        await message.reply_text("Найдено несколько вариантов. Выберите точку:", reply_markup=wizard_place_keyboard([point.label for point in candidates[:5]]))
        return True

    new_state = wizard_set_point(state, _pack_point(candidates[0]))
    await _show_wizard_params(message, context, new_state)
    return True


async def _run_wizard_product(message, context: ContextTypes.DEFAULT_TYPE, state: dict[str, object]) -> None:
    point_payload = state.get("point")
    if not isinstance(point_payload, dict):
        await message.reply_text("Сначала выберите точку.")
        return
    point = _unpack_point(point_payload)
    product = str(state.get("product", ""))
    context.user_data.pop(PRODUCT_WIZARD_KEY, None)

    if product == "aero":
        parsed = ParsedAeroRequest(
            location_query=f"{point.lat:.4f} {point.lon:.4f}",
            lead_hour=int(state.get("lead", DEFAULT_LEAD)),
            run=None,
            diagram_type=str(state.get("diagram_type", "stuve")),
        )
        await run_aero_product(message, point, parsed, GFS_SEMAPHORE)
        return

    if product == "windgram":
        parsed = ParsedWindgramRequest(
            location_query=f"{point.lat:.4f} {point.lon:.4f}",
            run=None,
            lead_from=int(state.get("from", 0)),
            lead_to=int(state.get("to", 120)),
            step=int(state.get("time_step", 6)),
            top_hpa=int(state.get("top", 500)),
            param=str(state.get("param", "wind")),
        )
        await run_windgram_product(message, point, parsed, GFS_SEMAPHORE)
        return

    if product == "cloudgram":
        parsed = ParsedCloudgramRequest(
            location_query=f"{point.lat:.4f} {point.lon:.4f}",
            run=None,
            lead_from=int(state.get("from", 0)),
            lead_to=int(state.get("to", 72)),
            step=int(state.get("time_step", 3)),
            mode=str(state.get("mode", "pro")),
        )
        await run_cloudgram_product(message, point, parsed, GFS_SEMAPHORE)
        return

    if product == "map":
        parsed = ParsedMapRequest(
            location_query=f"{point.lat:.4f} {point.lon:.4f}",
            run=None,
            lead_from=int(state.get("from", 0)) if bool(state.get("anim", False)) else int(state.get("lead", DEFAULT_LEAD)),
            lead_to=int(state.get("to", 24)) if bool(state.get("anim", False)) else int(state.get("lead", DEFAULT_LEAD)),
            step=int(state.get("time_step", 3)),
            animate=bool(state.get("anim", False)),
            radius_km=float(state.get("radius", 100)),
        )
        await run_map_product(message, point, parsed, GFS_SEMAPHORE)
        return

    await message.reply_text("Неизвестный продукт. Начните заново: /aero, /skewt, /windgram, /cloudgram или /map.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "🌦️ GFS 0.25 по точке\n"
        "Бот строит модельные продукты ближайшего узла GFS: профиль, аэродиаграммы, windgram и cloudgram.\n\n"
        "Быстро:\n"
        "• отправьте геолокацию или город — для профиля;\n"
        "• /cloudgram — облака, осадки, гроза, видимость;\n"
        "• /map — композитная карта вокруг точки;\n"
        "• /windgram — срок × уровень;\n"
        "• /aero или /skewt — аэрологическая диаграмма.\n\n"
        "После расчёта бот отдаёт PNG/CSV и команду для повтора.",
        reply_markup=location_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "Команды:\n"
        "<code>/profile Москва +24</code> — вертикальный профиль\n"
        "<code>/aero Москва +24 type=skewt</code> — аэродиаграмма\n"
        "<code>/windgram Москва to=120 step=6 param=temp</code> — срок × уровень\n"
        "<code>/cloudgram Москва to=72 step=3 mode=simple</code> — облака и явления\n\n"
        "<code>/map Москва +24</code> — композитная карта\n"
        "<code>/map Краснодар from=0 to=24 step=3 anim=1</code> — анимация карты\n\n"
        "Без параметров /aero, /skewt, /windgram, /cloudgram и /map запускают пошаговый выбор. Время на графиках — UTC.",
        parse_mode=ParseMode.HTML,
        reply_markup=location_keyboard(),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending(context)
    message = update.effective_message
    if message:
        await message.reply_text("Выбор сброшен. Отправьте город, координаты или геолокацию.", reply_markup=location_keyboard())


async def cycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    try:
        run = await asyncio.to_thread(latest_available_run)
        await message.reply_text(f"🕒 Последний цикл GFS: {run.date} {run.cycle}Z")
    except GfsProfileError as exc:
        await message.reply_text(str(exc))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    lines = ["⚙️ Статус GFS"]
    for lead in dict.fromkeys((0, DEFAULT_LEAD, 24, 48, 72, 120, 240, 384)):
        try:
            run = await asyncio.to_thread(latest_available_run_for_lead, lead)
            lines.append(f"+{lead:03d} ч → {run.date} {run.cycle}Z")
        except GfsProfileError:
            lines.append(f"+{lead:03d} ч → недоступно")
    lines.append("")
    lines.append(f"GFS-запросы: до {MAX_CONCURRENT_GFS} одновременно")
    lines.append(f"Геокодинг: до {MAX_CONCURRENT_GEOCODE} одновременно")
    lines.append(f"Кэш GRIB2: {CACHE_DIR}")
    await message.reply_text("\n".join(lines))


async def run_profile(message, point: GeoPoint, lead_hour: int, run: GfsRun | None = None) -> None:
    status = await message.reply_text(
        "⏳ Профиль GFS\n"
        f"📍 {point.label}\n"
        f"🕒 срок +{lead_hour} ч\n"
        "1/5 выбираю опубликованный цикл GFS…"
    )
    csv_path: Path | None = None
    png_path: Path | None = None
    selected_run: GfsRun | None = None
    try:
        async with GFS_SEMAPHORE:
            selected_run = run or await asyncio.to_thread(latest_available_run_for_lead, lead_hour)
            result = await build_profile_with_progress(status, selected_run, lead_hour, point)
            await status.edit_text("5/5 Профиль рассчитан. Готовлю PNG и CSV…")
            summary = format_profile_summary(result)
            csv_path = write_profile_csv(result)
            png_path = write_profile_png(result)
        await status.edit_text(summary, parse_mode=ParseMode.HTML)
        if png_path:
            with png_path.open("rb") as file_obj:
                await message.reply_photo(photo=InputFile(file_obj, filename=png_path.name), caption=f"PNG · PROFILE · GFS {selected_run.date} {selected_run.cycle}Z · +{lead_hour} ч · UTC")
        if csv_path:
            with csv_path.open("rb") as file_obj:
                await message.reply_document(document=InputFile(file_obj, filename=csv_path.name), caption=f"CSV · PROFILE · GFS {selected_run.date} {selected_run.cycle}Z · +{lead_hour} ч")
        if selected_run:
            await message.reply_text(_profile_repeat_message(point, lead_hour, selected_run), parse_mode=ParseMode.HTML)
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)
        if csv_path:
            csv_path.unlink(missing_ok=True)


async def resolve_profile_request(message, context: ContextTypes.DEFAULT_TYPE, raw: str) -> None:
    try:
        parsed = parse_request(raw)
        async with GEOCODE_SEMAPHORE:
            candidates = await asyncio.to_thread(search_location_candidates, parsed.location_query, 3)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return

    if not candidates:
        await message.reply_text("Точка не найдена. Пришлите координаты, город или геолокацию Telegram.")
        return

    if len(candidates) > 1:
        context.user_data["pending_candidates"] = {
            "candidates": [_pack_point(point) for point in candidates[:3]],
            "lead_hour": parsed.lead_hour,
            "run": _pack_run(parsed.run),
            "lead_from_user": parsed.lead_from_user,
        }
        await message.reply_text("Найдено несколько точек. Выберите нужную:", reply_markup=place_keyboard([point.label for point in candidates]))
        return

    point = candidates[0]
    if parsed.lead_from_user:
        await run_profile(message, point, parsed.lead_hour, parsed.run)
        return

    _set_pending_point(context, point, parsed.run)
    await message.reply_text(f"📍 Точка выбрана:\n{_point_brief(point)}\n\nВыберите срок прогноза:\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await message.reply_text("Укажите точку. Пример: /profile Москва +24 или /profile 55.75 37.62 +48")
        return
    await resolve_profile_request(message, context, raw)


async def aero_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_aero_wizard_state(DEFAULT_LEAD, "stuve"))
        return
    await resolve_aero_request(message, raw, DEFAULT_LEAD, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, default_diagram_type="stuve")


async def skewt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_aero_wizard_state(DEFAULT_LEAD, "skewt"))
        return
    await resolve_aero_request(message, raw, DEFAULT_LEAD, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, default_diagram_type="skewt")


async def windgram_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_windgram_wizard_state())
        return
    await resolve_windgram_request(message, raw, GFS_SEMAPHORE, GEOCODE_SEMAPHORE)


async def cloudgram_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_cloudgram_wizard_state())
        return
    await resolve_cloudgram_request(message, raw, GFS_SEMAPHORE, GEOCODE_SEMAPHORE)


async def map_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_map_wizard_state(DEFAULT_LEAD))
        return
    await resolve_map_request(message, raw, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, DEFAULT_LEAD)


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    text = message.text.strip()
    if text in {"❓ Помощь", "Помощь", "help"}:
        await help_command(update, context)
        return
    if await _resolve_wizard_point(message, context, text):
        return
    await resolve_profile_request(message, context, text)


async def location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.location:
        return
    point = GeoPoint(message.location.latitude, message.location.longitude, "геолокация Telegram", "telegram")
    state = _wizard_state(context)
    if state and state.get("step") in {"await_point", "choose_place"}:
        new_state = wizard_set_point(state, _pack_point(point))
        await _show_wizard_params(message, context, new_state)
        return
    _set_pending_point(context, point)
    await message.reply_text(f"📍 Геолокация получена:\n{_point_brief(point)}\n\nВыберите срок прогноза:\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


async def product_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    state = _wizard_state(context)
    data = query.data or ""
    if data == "wiz:cancel":
        context.user_data.pop(PRODUCT_WIZARD_KEY, None)
        await query.edit_message_text("Выбор отменён. Начните заново: /aero, /skewt, /windgram, /cloudgram или /map.")
        return
    if not state:
        await query.edit_message_text("Сценарий устарел. Начните заново: /aero, /skewt, /windgram, /cloudgram или /map.")
        return

    if data == "wiz:point":
        state = dict(state)
        state["step"] = "await_point"
        state.pop("point", None)
        state.pop("candidates", None)
        context.user_data[PRODUCT_WIZARD_KEY] = state
        await query.edit_message_text(point_prompt_text(state))
        return

    if data.startswith("wiz:place:"):
        index = int(data.rsplit(":", 1)[1])
        candidates = state.get("candidates", [])
        if not isinstance(candidates, list) or index < 0 or index >= len(candidates):
            await query.edit_message_text("Вариант точки устарел. Введите город или координаты ещё раз.")
            return
        point_payload = candidates[index]
        if not isinstance(point_payload, dict):
            await query.edit_message_text("Вариант точки повреждён. Повторите выбор.")
            return
        state = wizard_set_point(state, point_payload)
        context.user_data[PRODUCT_WIZARD_KEY] = state
        await query.edit_message_text(params_text(state), reply_markup=params_keyboard(state))
        return

    state = dict(state)
    if data.startswith("wiz:aero:type:"):
        state["diagram_type"] = data.rsplit(":", 1)[1]
    elif data.startswith("wiz:aero:lead:"):
        state["lead"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:wind:param:"):
        state["param"] = data.rsplit(":", 1)[1]
    elif data.startswith("wiz:wind:to:"):
        state["to"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:wind:step:"):
        state["time_step"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:wind:top:"):
        state["top"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:cloud:mode:"):
        state["mode"] = data.rsplit(":", 1)[1]
    elif data.startswith("wiz:cloud:to:"):
        state["to"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:cloud:step:"):
        state["time_step"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:map:anim:"):
        state["anim"] = data.rsplit(":", 1)[1] == "1"
    elif data.startswith("wiz:map:lead:"):
        lead = int(data.rsplit(":", 1)[1])
        state["lead"] = lead
        state["to"] = max(int(state.get("to", 24)), lead)
    elif data.startswith("wiz:map:to:"):
        state["to"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:map:step:"):
        state["time_step"] = int(data.rsplit(":", 1)[1])
    elif data == "wiz:run":
        if query.message:
            await query.edit_message_text("Параметры выбраны. Запускаю расчёт…")
            await _run_wizard_product(query.message, context, state)
        return
    else:
        return

    context.user_data[PRODUCT_WIZARD_KEY] = state
    await query.edit_message_text(params_text(state), reply_markup=params_keyboard(state))


async def lead_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if not data.startswith("lead:"):
        return
    pending = context.user_data.get("pending_profile")
    if not pending:
        await query.edit_message_text("Сначала выберите точку: город, координаты или геолокация.")
        return
    point = _unpack_point(pending["point"])
    run = _unpack_run(pending.get("run"))
    lead_hour = int(data.split(":", 1)[1])
    _clear_pending(context)
    if query.message:
        await query.edit_message_text(f"Срок +{lead_hour} ч выбран. Строю профиль…")
        await run_profile(query.message, point, lead_hour, run)


async def lead_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    pending = context.user_data.get("pending_profile")
    if not pending:
        await query.edit_message_text("Сначала выберите точку: город, координаты или геолокация.")
        return
    page = int((query.data or "leadpage:0").split(":", 1)[1])
    point = _unpack_point(pending["point"])
    await query.edit_message_text(f"📍 Точка:\n{_point_brief(point)}\n\nВыберите срок прогноза:\n{lead_page_text(page)}", reply_markup=lead_keyboard(page))


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer("Текущая страница")


async def place_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data == "cancel":
        _clear_pending(context)
        await query.edit_message_text("Выбор отменён. Отправьте город, координаты или геолокацию.")
        return
    if not data.startswith("place:"):
        return
    pending = context.user_data.get("pending_candidates")
    if not pending:
        await query.edit_message_text("Список вариантов устарел. Повторите запрос.")
        return
    index = int(data.split(":", 1)[1])
    candidates = pending.get("candidates", [])
    if index < 0 or index >= len(candidates):
        await query.edit_message_text("Некорректный вариант. Повторите запрос.")
        return
    point = _unpack_point(candidates[index])
    run = _unpack_run(pending.get("run"))
    lead_hour = int(pending.get("lead_hour", DEFAULT_LEAD))
    lead_from_user = bool(pending.get("lead_from_user", False))
    context.user_data.pop("pending_candidates", None)
    if not query.message:
        return
    if lead_from_user:
        await query.edit_message_text(f"Выбрано:\n{_point_brief(point)}\nСтрою профиль +{lead_hour} ч…")
        await run_profile(query.message, point, lead_hour, run)
        return
    _set_pending_point(context, point, run)
    await query.edit_message_text(f"📍 Выбрано:\n{_point_brief(point)}\n\nВыберите срок прогноза:\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN или BOT_TOKEN")
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("cycle", cycle_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("aero", aero_command))
    application.add_handler(CommandHandler("skewt", skewt_command))
    application.add_handler(CommandHandler("windgram", windgram_command))
    application.add_handler(CommandHandler("cloudgram", cloudgram_command))
    application.add_handler(CommandHandler("clouds", cloudgram_command))
    application.add_handler(CommandHandler("map", map_command))
    application.add_handler(MessageHandler(filters.LOCATION, location_message))
    application.add_handler(CallbackQueryHandler(product_wizard_callback, pattern=r"^wiz:"))
    application.add_handler(CallbackQueryHandler(lead_callback, pattern=r"^lead:\d+$"))
    application.add_handler(CallbackQueryHandler(lead_page_callback, pattern=r"^leadpage:\d+$"))
    application.add_handler(CallbackQueryHandler(noop_callback, pattern=r"^noop$"))
    application.add_handler(CallbackQueryHandler(place_callback, pattern=r"^(place:\d+|cancel)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    return application


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
