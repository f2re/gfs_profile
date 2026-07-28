from __future__ import annotations

import asyncio
import html
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from admin_stats import (
    add_admin_by_query,
    export_requests_csv,
    export_users_csv,
    find_users,
    format_admin_summary,
    format_recent_requests,
    format_users,
    is_admin,
    record_request_finish,
    record_request_start,
    record_telegram_user,
)
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
from user_location_session import RECENT_LOCATION_PREFIX, get_recent_locations, match_recent_location_button, remember_location

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


def _user_id_from_message(message) -> int:
    user = getattr(message, "from_user", None)
    return int(getattr(user, "id", 0) or 0)


def _user_id_from_update(update: Update) -> int:
    user = update.effective_user
    return int(getattr(user, "id", 0) or 0)


def _record_update_user(update: Update) -> None:
    record_telegram_user(update.effective_user)


def _location_keyboard_for_user(user_id: int):
    return location_keyboard(get_recent_locations(user_id))


def _set_pending_point(context: ContextTypes.DEFAULT_TYPE, point: GeoPoint, run: GfsRun | None = None) -> None:
    context.user_data["pending_profile"] = {"point": _pack_point(point), "run": _pack_run(run)}


def _point_brief(point: GeoPoint) -> str:
    return f"{point.label}\n{point.lat:.4f}, {point.lon:.4f}"


def _wizard_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, object] | None:
    state = context.user_data.get(PRODUCT_WIZARD_KEY)
    return state if isinstance(state, dict) else None


def _profile_command(point: GeoPoint, lead_hour: int, run: GfsRun) -> str:
    return f"/profile {point.lat:.4f} {point.lon:.4f} run={run.date}/{run.cycle} +{lead_hour}"


def _profile_request_text(point: GeoPoint, lead_hour: int, run: GfsRun | None) -> str:
    if run:
        return _profile_command(point, lead_hour, run)
    return f"/profile {point.lat:.4f} {point.lon:.4f} +{lead_hour}"


def _profile_repeat_message(point: GeoPoint, lead_hour: int, run: GfsRun) -> str:
    command = html.escape(_profile_command(point, lead_hour, run))
    return "📋 Повторить профиль:\n" f"<code>{command}</code>\n\n" "Нажмите на строку команды и скопируйте её целиком."


async def _track_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _record_update_user(update)


async def _tracked_product(
    message,
    product: str,
    city: str | None,
    request_text: str | None,
    runner,
    *,
    lead_from: int | None = None,
    lead_to: int | None = None,
    run: GfsRun | None = None,
    user=None,
):
    user = user or getattr(message, "from_user", None)
    user_id = int(getattr(user, "id", 0) or 0) or None
    username = getattr(user, "username", None)
    started = time.perf_counter()
    request_id = record_request_start(
        product=product,
        user_id=user_id,
        username=username,
        city=city,
        request_text=request_text,
        lead_from=lead_from,
        lead_to=lead_to,
        run_date=run.date if run else None,
        run_cycle=run.cycle if run else None,
    )
    try:
        result = await runner()
        status = "ok" if result is not False else "failed"
        record_request_finish(request_id, status=status, duration_ms=int((time.perf_counter() - started) * 1000))
        return result
    except Exception as exc:
        record_request_finish(request_id, status="error", duration_ms=int((time.perf_counter() - started) * 1000), error=str(exc)[:500])
        raise


async def _tracked_run_profile(message, point: GeoPoint, lead_hour: int, run: GfsRun | None = None, request_text: str | None = None, user=None) -> bool:
    return bool(
        await _tracked_product(
            message,
            "profile",
            point.label,
            request_text or _profile_request_text(point, lead_hour, run),
            lambda: run_profile(message, point, lead_hour, run),
            lead_from=lead_hour,
            lead_to=lead_hour,
            run=run,
            user=user,
        )
    )


async def _send_admin_csv(message, filename: str, content: str, caption: str) -> None:
    payload = BytesIO(content.encode("utf-8-sig"))
    await message.reply_document(document=InputFile(payload, filename=filename), caption=caption)


def _safe_int_arg(args: list[str], index: int, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(args[index])
    except (IndexError, ValueError):
        return default
    return max(minimum, min(value, maximum))


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _record_update_user(update)
    message = update.effective_message
    if not message:
        return
    user_id = _user_id_from_update(update)
    if not is_admin(user_id):
        await message.reply_text(
            "Доступ закрыт. Укажите TELEGRAM_ADMIN_IDS в .env или попросите действующего администратора добавить ваш id через /admin add."
        )
        return

    args = list(context.args or [])
    if not args:
        await message.reply_text(format_admin_summary(7), parse_mode=ParseMode.HTML)
        return

    action = args[0].lower()
    if action in {"help", "?", "помощь"}:
        await message.reply_text(
            "<pre>ADMIN\n"
            "/admin — сводка за 7 дней\n"
            "/admin stats [days] — сводка\n"
            "/admin recent [n] — последние запросы\n"
            "/admin users [query] — пользователи\n"
            "/admin find &lt;id|@user|name&gt; — поиск по известным пользователям\n"
            "/admin add &lt;id|@user&gt; — добавить администратора\n"
            "/admin report requests [days] — CSV запросов\n"
            "/admin report users — CSV пользователей\n\n"
            "Важно: Telegram Bot API не ищет пользователей глобально. Поиск работает по тем, кто уже писал боту.</pre>",
            parse_mode=ParseMode.HTML,
        )
        return

    if action in {"stats", "stat", "стат"}:
        days = _safe_int_arg(args, 1, 7, 1, 3650)
        await message.reply_text(format_admin_summary(days), parse_mode=ParseMode.HTML)
        return

    if action in {"recent", "requests", "req", "запросы"}:
        limit = _safe_int_arg(args, 1, 10, 1, 50)
        await message.reply_text(format_recent_requests(limit), parse_mode=ParseMode.HTML)
        return

    if action in {"users", "user", "пользователи"}:
        if len(args) > 1 and args[1].lower() in {"csv", "report", "отчет", "отчёт"}:
            await _send_admin_csv(message, "gfs_bot_users.csv", export_users_csv(), "CSV · пользователи GFS bot")
            return
        query = " ".join(args[1:])
        await message.reply_text(format_users(find_users(query, limit=30)), parse_mode=ParseMode.HTML)
        return

    if action in {"find", "search", "найти"}:
        query = " ".join(args[1:]).strip()
        if not query:
            await message.reply_text("Формат: /admin find <id|@username|имя>")
            return
        await message.reply_text(format_users(find_users(query, limit=20)), parse_mode=ParseMode.HTML)
        return

    if action in {"add", "admin", "админ"}:
        query = " ".join(args[1:]).strip()
        if not query:
            await message.reply_text("Формат: /admin add <id|@username>. Пользователь должен хотя бы раз написать боту.")
            return
        try:
            user = add_admin_by_query(query, added_by=user_id)
        except ValueError as exc:
            await message.reply_text(str(exc))
            return
        username = f"@{user.username}" if user.username else "без username"
        await message.reply_text(f"Администратор добавлен: {user.user_id} · {username}")
        return

    if action in {"report", "reports", "csv", "download", "отчет", "отчёт"}:
        kind = args[1].lower() if len(args) > 1 else "requests"
        if kind in {"users", "user", "пользователи"}:
            await _send_admin_csv(message, "gfs_bot_users.csv", export_users_csv(), "CSV · пользователи GFS bot")
            return
        days = _safe_int_arg(args, 2, 30, 1, 3650)
        await _send_admin_csv(message, f"gfs_bot_requests_{days}d.csv", export_requests_csv(days), f"CSV · запросы GFS bot · {days} дней")
        return

    await message.reply_text("Неизвестная admin-команда. Справка: /admin help")


async def _start_product_wizard(message, context: ContextTypes.DEFAULT_TYPE, state: dict[str, object]) -> None:
    _clear_pending(context)
    context.user_data[PRODUCT_WIZARD_KEY] = state
    await message.reply_text(point_prompt_text(state), reply_markup=_location_keyboard_for_user(_user_id_from_message(message)))


async def _show_wizard_params(message, context: ContextTypes.DEFAULT_TYPE, state: dict[str, object]) -> None:
    context.user_data[PRODUCT_WIZARD_KEY] = state
    await message.reply_text(params_text(state), reply_markup=params_keyboard(state))


async def _resolve_wizard_point(message, context: ContextTypes.DEFAULT_TYPE, raw: str) -> bool:
    state = _wizard_state(context)
    if not state or state.get("step") != "await_point":
        return False
    user_id = _user_id_from_message(message)
    recent_point = match_recent_location_button(user_id, raw)
    if recent_point is not None:
        remember_location(user_id, recent_point)
        new_state = wizard_set_point(state, _pack_point(recent_point))
        await _show_wizard_params(message, context, new_state)
        return True
    if raw.startswith(RECENT_LOCATION_PREFIX):
        await message.reply_text("Эта последняя точка уже недоступна. Введите город/координаты или отправьте геолокацию.")
        return True
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

    remember_location(user_id, candidates[0])
    new_state = wizard_set_point(state, _pack_point(candidates[0]))
    await _show_wizard_params(message, context, new_state)
    return True


async def _run_wizard_product(message, context: ContextTypes.DEFAULT_TYPE, state: dict[str, object], user=None) -> None:
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
        await _tracked_product(
            message,
            "aero",
            point.label,
            f"/aero {parsed.location_query} +{parsed.lead_hour} type={parsed.diagram_type}",
            lambda: run_aero_product(message, point, parsed, GFS_SEMAPHORE),
            lead_from=parsed.lead_hour,
            lead_to=parsed.lead_hour,
            run=parsed.run,
            user=user,
        )
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
        await _tracked_product(
            message,
            "windgram",
            point.label,
            f"/windgram {parsed.location_query} from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} top={parsed.top_hpa} param={parsed.param}",
            lambda: run_windgram_product(message, point, parsed, GFS_SEMAPHORE),
            lead_from=parsed.lead_from,
            lead_to=parsed.lead_to,
            run=parsed.run,
            user=user,
        )
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
        await _tracked_product(
            message,
            "cloudgram",
            point.label,
            f"/cloudgram {parsed.location_query} from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode={parsed.mode}",
            lambda: run_cloudgram_product(message, point, parsed, GFS_SEMAPHORE),
            lead_from=parsed.lead_from,
            lead_to=parsed.lead_to,
            run=parsed.run,
            user=user,
        )
        return

    if product == "map":
        map_mode = str(state.get("mode", "single"))
        parsed = ParsedMapRequest(
            location_query=f"{point.lat:.4f} {point.lon:.4f}",
            run=None,
            lead_from=int(state.get("from", 0)) if map_mode in {"series", "gif"} else int(state.get("lead", DEFAULT_LEAD)),
            lead_to=int(state.get("to", 24)) if map_mode in {"series", "gif"} else int(state.get("lead", DEFAULT_LEAD)),
            step=int(state.get("time_step", 3)),
            animate=map_mode == "gif",
            radius_km=float(state.get("radius", 100)),
            mode=map_mode,
            basemap=str(state.get("basemap", "places")),
        )
        await _tracked_product(
            message,
            "map",
            point.label,
            f"/map {parsed.location_query} from={parsed.lead_from} to={parsed.lead_to} step={parsed.step} mode={parsed.mode} basemap={parsed.basemap}",
            lambda: run_map_product(message, point, parsed, GFS_SEMAPHORE),
            lead_from=parsed.lead_from,
            lead_to=parsed.lead_to,
            run=parsed.run,
            user=user,
        )
        return

    await message.reply_text("Неизвестный продукт. Начните заново: /aero, /skewt, /windgram, /cloudgram или /map.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "🌦️ GFS 0.25 по точке\n"
        "Бот строит модельные продукты ближайшего узла GFS: профиль, аэродиаграммы, windgram, cloudgram и карту.\n\n"
        "Быстро:\n"
        "• отправьте геолокацию или город — для профиля;\n"
        "• /cloudgram — облака, осадки, гроза, видимость;\n"
        "• /map — композитная карта вокруг точки: одна PNG, серия PNG или GIF;\n"
        "• /windgram — срок × уровень;\n"
        "• /aero или /skewt — аэрологическая диаграмма.\n\n"
        "После расчёта бот отдаёт PNG/GIF/CSV и команду для повтора.",
        reply_markup=_location_keyboard_for_user(_user_id_from_update(update)),
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
        "<code>/map Москва +24</code> — одна композитная карта\n"
        "<code>/map Краснодар from=0 to=24 step=3 mode=series</code> — серия PNG отдельными картами\n"
        "<code>/map Краснодар from=0 to=24 step=3 mode=gif</code> — GIF-анимация\n"
        "<code>/map Москва +24 basemap=roads</code> — подложка: basic|water|places|roads\n\n"
        "/map объединяет осадки, облачность, грозовой риск, значки явлений, видимость и ветер AT500. Подложка читается из локального Natural Earth cache; если слой отсутствует, карта строится с fallback и пометкой в footer. GFS — модель, не наблюдения. Без параметров /aero, /skewt, /windgram, /cloudgram и /map запускают пошаговый выбор. Время — UTC.",
        parse_mode=ParseMode.HTML,
        reply_markup=_location_keyboard_for_user(_user_id_from_update(update)),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending(context)
    message = update.effective_message
    if message:
        await message.reply_text("Выбор сброшен. Отправьте город, координаты или геолокацию.", reply_markup=_location_keyboard_for_user(_user_id_from_update(update)))


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


async def run_profile(message, point: GeoPoint, lead_hour: int, run: GfsRun | None = None) -> bool:
    status = await message.reply_text(
        "⏳ Профиль GFS\n"
        f"📍 {point.label}\n"
        f"🕒 срок +{lead_hour} ч\n"
        "1/5 выбираю опубликованный цикл GFS…"
    )
    csv_path: Path | None = None
    png_path: Path | None = None
    selected_run: GfsRun | None = None
    success = False
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
        success = True
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)
        if csv_path:
            csv_path.unlink(missing_ok=True)
    return success


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
    remember_location(_user_id_from_message(message), point)
    if parsed.lead_from_user:
        await _tracked_run_profile(message, point, parsed.lead_hour, parsed.run, request_text=f"/profile {raw}")
        return
    _set_pending_point(context, point, parsed.run)
    await message.reply_text(f"📍 Точка выбрана:\n{_point_brief(point)}\n\nВыберите срок прогноза:\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await message.reply_text(
            "Профиль GFS\n"
            "Шаг 1/3 — точка\n\n"
            "Выберите точку: отправьте геолокацию, нажмите последнюю локацию или введите город/координаты.\n\n"
            "Примеры:\nМосква\n55.75 37.62\nКраснодар",
            reply_markup=_location_keyboard_for_user(_user_id_from_update(update)),
        )
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
    await _tracked_product(
        message,
        "aero",
        raw,
        f"/aero {raw}",
        lambda: resolve_aero_request(message, raw, DEFAULT_LEAD, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, default_diagram_type="stuve", user_id=_user_id_from_update(update)),
    )


async def skewt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_aero_wizard_state(DEFAULT_LEAD, "skewt"))
        return
    await _tracked_product(
        message,
        "skewt",
        raw,
        f"/skewt {raw}",
        lambda: resolve_aero_request(message, raw, DEFAULT_LEAD, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, default_diagram_type="skewt", user_id=_user_id_from_update(update)),
    )


async def windgram_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_windgram_wizard_state())
        return
    await _tracked_product(
        message,
        "windgram",
        raw,
        f"/windgram {raw}",
        lambda: resolve_windgram_request(message, raw, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, user_id=_user_id_from_update(update)),
    )


async def cloudgram_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_cloudgram_wizard_state())
        return
    await _tracked_product(
        message,
        "cloudgram",
        raw,
        f"/cloudgram {raw}",
        lambda: resolve_cloudgram_request(message, raw, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, user_id=_user_id_from_update(update)),
    )


async def map_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await _start_product_wizard(message, context, start_map_wizard_state(DEFAULT_LEAD))
        return
    await _tracked_product(
        message,
        "map",
        raw,
        f"/map {raw}",
        lambda: resolve_map_request(message, raw, GFS_SEMAPHORE, GEOCODE_SEMAPHORE, DEFAULT_LEAD, user_id=_user_id_from_update(update)),
    )


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
    user_id = _user_id_from_update(update)
    recent_point = match_recent_location_button(user_id, text)
    if recent_point is not None:
        remember_location(user_id, recent_point)
        _set_pending_point(context, recent_point)
        await message.reply_text(f"📍 Точка выбрана:\n{_point_brief(recent_point)}\n\nВыберите срок прогноза:\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))
        return
    if text.startswith(RECENT_LOCATION_PREFIX):
        await message.reply_text("Эта последняя точка уже недоступна. Введите город/координаты или отправьте геолокацию.")
        return
    await resolve_profile_request(message, context, text)


async def location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.location:
        return
    point = GeoPoint(message.location.latitude, message.location.longitude, "геолокация Telegram", "telegram")
    user_id = _user_id_from_update(update)
    remember_location(user_id, point)
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
        point = _unpack_point(point_payload)
        remember_location(_user_id_from_update(update), point)
        state = wizard_set_point(state, _pack_point(point))
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
    elif data.startswith("wiz:map:mode:"):
        state["mode"] = data.rsplit(":", 1)[1]
    elif data.startswith("wiz:map:lead:"):
        lead = int(data.rsplit(":", 1)[1])
        state["lead"] = lead
        state["to"] = max(int(state.get("to", 24)), lead)
    elif data.startswith("wiz:map:from:"):
        value = int(data.rsplit(":", 1)[1])
        state["from"] = value
        state["to"] = max(int(state.get("to", 24)), value)
    elif data.startswith("wiz:map:to:"):
        value = int(data.rsplit(":", 1)[1])
        state["to"] = value
        state["from"] = min(int(state.get("from", 0)), value)
    elif data.startswith("wiz:map:step:"):
        state["time_step"] = int(data.rsplit(":", 1)[1])
    elif data.startswith("wiz:map:basemap:"):
        state["basemap"] = data.rsplit(":", 1)[1]
    elif data == "wiz:run":
        if query.message:
            await query.edit_message_text("Параметры выбраны. Запускаю расчёт…")
            await _run_wizard_product(query.message, context, state, user=update.effective_user)
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
        await _tracked_run_profile(query.message, point, lead_hour, run, user=update.effective_user)


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
    remember_location(_user_id_from_update(update), point)
    run = _unpack_run(pending.get("run"))
    lead_hour = int(pending.get("lead_hour", DEFAULT_LEAD))
    lead_from_user = bool(pending.get("lead_from_user", False))
    context.user_data.pop("pending_candidates", None)
    if not query.message:
        return
    if lead_from_user:
        await query.edit_message_text(f"Выбрано:\n{_point_brief(point)}\nСтрою профиль +{lead_hour} ч…")
        await _tracked_run_profile(query.message, point, lead_hour, run, user=update.effective_user)
        return
    _set_pending_point(context, point, run)
    await query.edit_message_text(f"📍 Выбрано:\n{_point_brief(point)}\n\nВыберите срок прогноза:\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN или BOT_TOKEN")
    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.ALL, _track_update), group=-1)
    application.add_handler(CallbackQueryHandler(_track_update), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("cycle", cycle_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("admin", admin_command))
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
