from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from admin_stats import record_request_finish, record_request_start, record_telegram_user
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead, validate_lead
from product_progress import run_product_with_progress
from route_profile import (
    ROUTE_DEFAULT_SPEED_KMH,
    ROUTE_MAX_SPEED_KMH,
    ROUTE_MIN_SPEED_KMH,
    RouteProfileData,
    write_route_csv,
)
from route_profile_contract import (
    ROUTE_SPATIAL_STEP_KM,
    ROUTE_SPATIAL_STEPS_KM,
    build_route_profile_data,
    route_summary,
    route_waypoint_specs,
    validate_spatial_step,
)
import route_profile_vertical_policy  # noqa: F401
from route_profile_plot import write_route_profile_png
from telegram_file_send import reply_png_file
from user_location_session import remember_location

ROUTE_SESSION_KEY = "route_profile_wizard"
RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
SPEED_RE = re.compile(r"\b(?:speed|v|скорость)=(?P<value>\d{2,4})\b", re.IGNORECASE)
MODE_RE = re.compile(r"\bmode=(?P<value>simple|pro|простой|профи)\b", re.IGNORECASE)
STEP_RE = re.compile(r"\b(?:step|grid|шаг)=(?P<value>25|50|100)(?:\s*км)?\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:\blead=|\+)(?P<value>\d{1,3})(?:\s*(?:h|ч))?\b", re.IGNORECASE)
ROUTE_SPLIT_RE = re.compile(r"\s*(?:->|→|=>|\|)\s*")

ROUTE_LEADS = (0, 6, 12, 24, 48)
ROUTE_SPEEDS = (150, 300, 450, 600)
ROUTE_MODES = (("simple", "Простой"), ("pro", "Профи"))
ROUTE_LONG_POINT_WARNING = 60

_GFS_SEMAPHORE = None
_GEOCODE_SEMAPHORE = None


@dataclass(frozen=True)
class ParsedRouteRequest:
    origin_query: str
    destination_query: str
    departure_lead: int = 24
    speed_kmh: int = ROUTE_DEFAULT_SPEED_KMH
    mode: str = "simple"
    run: GfsRun | None = None
    spatial_step_km: int = int(ROUTE_SPATIAL_STEP_KM)
    step_explicit: bool = False


def _normalize_mode(value: str) -> str:
    return "pro" if value.lower() in {"pro", "профи"} else "simple"


def parse_route_request(raw_text: str, default_lead: int = 24) -> ParsedRouteRequest:
    text = raw_text.strip()
    run = None
    match = RUN_RE.search(text)
    if match:
        run = GfsRun(match.group("date"), match.group("cycle"))
        text = (text[: match.start()] + text[match.end() :]).strip()

    speed = ROUTE_DEFAULT_SPEED_KMH
    match = SPEED_RE.search(text)
    if match:
        speed = int(match.group("value"))
        text = (text[: match.start()] + text[match.end() :]).strip()
    if not ROUTE_MIN_SPEED_KMH <= speed <= ROUTE_MAX_SPEED_KMH:
        raise ValueError(f"Скорость должна быть {ROUTE_MIN_SPEED_KMH}…{ROUTE_MAX_SPEED_KMH} км/ч")

    mode = "simple"
    match = MODE_RE.search(text)
    if match:
        mode = _normalize_mode(match.group("value"))
        text = (text[: match.start()] + text[match.end() :]).strip()

    spatial_step = int(ROUTE_SPATIAL_STEP_KM)
    step_explicit = False
    match = STEP_RE.search(text)
    if match:
        spatial_step = validate_spatial_step(int(match.group("value")))
        step_explicit = True
        text = (text[: match.start()] + text[match.end() :]).strip()

    lead = int(default_lead)
    match = LEAD_RE.search(text)
    if match:
        lead = int(match.group("value"))
        text = (text[: match.start()] + text[match.end() :]).strip()
    validate_lead(lead)

    parts = [part.strip(" ,;") for part in ROUTE_SPLIT_RE.split(text, maxsplit=1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Маршрут задаётся через → или ->. Пример: Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro")
    return ParsedRouteRequest(parts[0], parts[1], lead, speed, mode, run, spatial_step, step_explicit)


def _pack_point(point: GeoPoint) -> dict[str, object]:
    return {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source}


def _unpack_point(payload: dict[str, object]) -> GeoPoint:
    return GeoPoint(float(payload["lat"]), float(payload["lon"]), str(payload["label"]), str(payload.get("source", "route")))


def _route_plan(origin: GeoPoint, destination: GeoPoint, lead: int, speed: int, spatial_step: int):
    return route_waypoint_specs(origin, destination, lead, speed, spatial_step_km=validate_spatial_step(spatial_step))


def _point_count_for_step(origin: GeoPoint, destination: GeoPoint, lead: int, speed: int, step: int) -> int:
    return len(_route_plan(origin, destination, lead, speed, step)[2])


def _long_route_warning(point_count: int, spatial_step: int) -> str:
    if point_count < ROUTE_LONG_POINT_WARNING:
        return ""
    return (
        f"\n⚠️ Детальная сетка {spatial_step} км: {point_count} расчётных точек. "
        "Расчёт может занять заметно больше времени. Можно выбрать 50 или 100 км.\n"
    )


def _settings_state(origin: GeoPoint, destination: GeoPoint, parsed: ParsedRouteRequest) -> dict[str, object]:
    return {
        "step": "settings",
        "origin": _pack_point(origin),
        "destination": _pack_point(destination),
        "lead": parsed.departure_lead,
        "speed": parsed.speed_kmh,
        "mode": parsed.mode,
        "spatial_step": parsed.spatial_step_km,
    }


def route_settings_text(state: dict[str, object]) -> str:
    origin = _unpack_point(state["origin"])
    destination = _unpack_point(state["destination"])
    speed = int(state.get("speed", ROUTE_DEFAULT_SPEED_KMH))
    lead = int(state.get("lead", 24))
    mode = str(state.get("mode", "simple"))
    spatial_step = validate_spatial_step(int(state.get("spatial_step", ROUTE_SPATIAL_STEP_KM)))
    distance, duration, specs = _route_plan(origin, destination, lead, speed, spatial_step)
    max_lead = max(item[4] for item in specs)
    mode_name = "Профи" if mode == "pro" else "Простой"
    alternatives = " · ".join(
        f"{step} км ≈{_point_count_for_step(origin, destination, lead, speed, step)} т."
        for step in ROUTE_SPATIAL_STEPS_KM
    )
    warning = _long_route_warning(len(specs), spatial_step)
    return (
        "✈️ Маршрутный профиль GFS\n"
        "Шаг 2/2 — параметры\n\n"
        f"🧭 {origin.label} → {destination.label}\n"
        f"📏 {distance:.0f} км · расчётное время {duration:.1f} ч\n"
        f"🕒 вылет через +{lead} ч · прибытие около +{max_lead} ч\n"
        f"🚀 средняя скорость {speed} км/ч\n"
        f"🧩 сетка {spatial_step} км · {len(specs)} точек\n"
        f"📊 режим {mode_name} · профиль до 500 гПа\n"
        f"{warning}\n"
        f"Детализация: {alternatives}\n\n"
        "25 км — максимум деталей; 50/100 км — быстрее для длинного маршрута. "
        "Риск считается одинаково в simple/pro, меняется только отображение."
    )


def route_settings_keyboard(state: dict[str, object]) -> InlineKeyboardMarkup:
    lead = int(state.get("lead", 24))
    speed = int(state.get("speed", ROUTE_DEFAULT_SPEED_KMH))
    mode = str(state.get("mode", "simple"))
    spatial_step = int(state.get("spatial_step", ROUTE_SPATIAL_STEP_KM))
    lead_buttons = [InlineKeyboardButton(("✓ " if lead == value else "") + f"вылет +{value}ч", callback_data=f"route:lead:{value}") for value in ROUTE_LEADS]
    return InlineKeyboardMarkup([
        lead_buttons[:3],
        lead_buttons[3:],
        [InlineKeyboardButton(("✓ " if speed == value else "") + f"{value} км/ч", callback_data=f"route:speed:{value}") for value in ROUTE_SPEEDS],
        [InlineKeyboardButton(("✓ " if spatial_step == value else "") + f"сетка {value} км", callback_data=f"route:grid:{value}") for value in ROUTE_SPATIAL_STEPS_KM],
        [InlineKeyboardButton(("✓ " if mode == key else "") + label, callback_data=f"route:mode:{key}") for key, label in ROUTE_MODES],
        [InlineKeyboardButton("▶ Построить", callback_data="route:run")],
        [InlineKeyboardButton("↩ Другой маршрут", callback_data="route:restart"), InlineKeyboardButton("Отмена", callback_data="route:cancel")],
    ])


async def _resolve_endpoint(query: str) -> GeoPoint:
    if _GEOCODE_SEMAPHORE is None:
        candidates = await asyncio.to_thread(search_location_candidates, query, 1)
    else:
        async with _GEOCODE_SEMAPHORE:
            candidates = await asyncio.to_thread(search_location_candidates, query, 1)
    if not candidates:
        raise GeocodeError(f"Точка не найдена: {query}")
    candidate = candidates[0]
    if candidate.source == "coordinates":
        label = f"{candidate.lat:.4f} {candidate.lon:.4f}"
    else:
        label = re.sub(r"\s+", " ", query.strip()) or candidate.label
    return GeoPoint(candidate.lat, candidate.lon, label, candidate.source)


async def _resolve_route(parsed: ParsedRouteRequest) -> tuple[GeoPoint, GeoPoint]:
    origin, destination = await asyncio.gather(_resolve_endpoint(parsed.origin_query), _resolve_endpoint(parsed.destination_query))
    return origin, destination


def _command_endpoint(point: GeoPoint) -> str:
    label = re.sub(r"\s+", " ", point.label.strip())
    label = label.replace("→", "-").replace("->", "-")
    return label or f"{point.lat:.4f} {point.lon:.4f}"


def _repeat_command(data: RouteProfileData, parsed: ParsedRouteRequest) -> str:
    return (
        f"/route {_command_endpoint(data.origin)} -> {_command_endpoint(data.destination)} "
        f"+{data.departure_lead} speed={data.speed_kmh} step={parsed.spatial_step_km} "
        f"mode={data.mode} run={data.run.date}/{data.run.cycle}"
    )


async def run_route_product(message, origin: GeoPoint, destination: GeoPoint, parsed: ParsedRouteRequest, user=None) -> bool:
    distance, duration, specs = _route_plan(origin, destination, parsed.departure_lead, parsed.speed_kmh, parsed.spatial_step_km)
    max_lead = max(item[4] for item in specs)
    selected_run = parsed.run or await asyncio.to_thread(latest_available_run_for_lead, max_lead)
    warning = _long_route_warning(len(specs), parsed.spatial_step_km)
    status = await message.reply_text(
        "⏳ Маршрутный профиль GFS\n"
        f"🧭 {origin.label} → {destination.label}\n"
        f"📏 {distance:.0f} км · {duration:.1f} ч · {parsed.speed_kmh} км/ч\n"
        f"🧩 сетка {parsed.spatial_step_km} км · {len(specs)} точек\n"
        f"🕒 вылет +{parsed.departure_lead} ч · до +{max_lead} ч\n"
        f"{warning}Подготавливаю точки маршрута…"
    )
    png_path: Path | None = None
    csv_path: Path | None = None
    user_id = int(getattr(user or getattr(message, "from_user", None), "id", 0) or 0) or None
    username = getattr(user or getattr(message, "from_user", None), "username", None)
    started = time.perf_counter()
    request_id = record_request_start(
        product="route",
        user_id=user_id,
        username=username,
        city=f"{origin.label} → {destination.label}",
        request_text=f"/route {parsed.origin_query} -> {parsed.destination_query} +{parsed.departure_lead} speed={parsed.speed_kmh} step={parsed.spatial_step_km} mode={parsed.mode}",
        lead_from=parsed.departure_lead,
        lead_to=max_lead,
        run_date=selected_run.date,
        run_cycle=selected_run.cycle,
    )
    try:
        header = (
            f"✈️ ROUTE · {parsed.mode}\n"
            f"GFS {selected_run.date} {selected_run.cycle}Z · {origin.label} → {destination.label}\n"
            f"{distance:.0f} км · шаг {parsed.spatial_step_km} км · {len(specs)} точек · +{parsed.departure_lead}…+{max_lead} ч"
        )

        def worker(progress_callback):
            data = build_route_profile_data(
                selected_run,
                origin,
                destination,
                parsed.departure_lead,
                speed_kmh=parsed.speed_kmh,
                mode=parsed.mode,
                progress_callback=progress_callback,
                spatial_step_km=parsed.spatial_step_km,
            )
            progress_callback({"stage": "plot_start", "message": "строю маршрутный PNG"})
            png = write_route_profile_png(data)
            csv_file = write_route_csv(data)
            progress_callback({"stage": "plot_done", "message": "PNG и CSV готовы"})
            return data, png, csv_file

        if _GFS_SEMAPHORE is None:
            data, png_path, csv_path = await run_product_with_progress(status, header, worker)
        else:
            async with _GFS_SEMAPHORE:
                data, png_path, csv_path = await run_product_with_progress(status, header, worker)
        await status.edit_text(route_summary(data))
        await reply_png_file(
            message,
            png_path,
            caption=f"PNG · ROUTE {parsed.mode.upper()} · GFS {selected_run.date} {selected_run.cycle}Z · {origin.label} → {destination.label} · {distance:.0f} км · сетка {parsed.spatial_step_km} км",
            prefer_photo=True,
        )
        with csv_path.open("rb") as file_obj:
            await message.reply_document(document=InputFile(file_obj, filename=csv_path.name), caption="CSV · точки маршрута, ETA, уровни до 500 гПа и диагностические риски")
        command = html.escape(_repeat_command(data, parsed))
        await message.reply_text("📋 Повторить расчёт:\n" f"<code>{command}</code>", parse_mode=ParseMode.HTML)
        record_request_finish(request_id, status="ok", duration_ms=int((time.perf_counter() - started) * 1000))
        return True
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        record_request_finish(request_id, status="failed", duration_ms=int((time.perf_counter() - started) * 1000), error=str(exc)[:500])
        await status.edit_text(f"Ошибка: {exc}")
        return False
    except Exception as exc:
        record_request_finish(request_id, status="error", duration_ms=int((time.perf_counter() - started) * 1000), error=str(exc)[:500])
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
        return False
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)
        if csv_path:
            csv_path.unlink(missing_ok=True)


async def route_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_telegram_user(update.effective_user)
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args or []).strip()
    if not raw:
        context.user_data[ROUTE_SESSION_KEY] = {"step": "await_route", "lead": 24, "speed": ROUTE_DEFAULT_SPEED_KMH, "mode": "simple", "spatial_step": int(ROUTE_SPATIAL_STEP_KM)}
        await message.reply_text(
            "✈️ Маршрутный профиль GFS\n"
            "Шаг 1/2 — маршрут\n\n"
            "Введите начало и конец через → или ->. Можно использовать города или координаты.\n\n"
            "Примеры:\n"
            "Москва -> Санкт-Петербург\n"
            "55.75 37.62 -> 59.94 30.31\n\n"
            "Далее выберите срок, скорость, режим и сетку 25/50/100 км."
        )
        return
    try:
        parsed = parse_route_request(raw)
        origin, destination = await _resolve_route(parsed)
        remember_location(int(getattr(update.effective_user, "id", 0) or 0), origin)
        remember_location(int(getattr(update.effective_user, "id", 0) or 0), destination)
        point_count = _point_count_for_step(origin, destination, parsed.departure_lead, parsed.speed_kmh, parsed.spatial_step_km)
        if point_count >= ROUTE_LONG_POINT_WARNING and not parsed.step_explicit:
            state = _settings_state(origin, destination, parsed)
            context.user_data[ROUTE_SESSION_KEY] = state
            await message.reply_text(route_settings_text(state), reply_markup=route_settings_keyboard(state))
            return
        await run_route_product(message, origin, destination, parsed, user=update.effective_user)
    except (ValueError, GeocodeError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")


async def route_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get(ROUTE_SESSION_KEY)
    if not isinstance(state, dict) or state.get("step") != "await_route":
        return
    record_telegram_user(update.effective_user)
    message = update.effective_message
    if not message or not message.text:
        return
    try:
        parsed = parse_route_request(message.text, default_lead=int(state.get("lead", 24)))
        origin, destination = await _resolve_route(parsed)
        user_id = int(getattr(update.effective_user, "id", 0) or 0)
        remember_location(user_id, origin)
        remember_location(user_id, destination)
        state = _settings_state(origin, destination, parsed)
        context.user_data[ROUTE_SESSION_KEY] = state
        await message.reply_text(route_settings_text(state), reply_markup=route_settings_keyboard(state))
    except (ValueError, GeocodeError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}\nПовторите маршрут: Москва -> Санкт-Петербург")
    raise ApplicationHandlerStop


async def route_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    record_telegram_user(update.effective_user)
    state = context.user_data.get(ROUTE_SESSION_KEY)
    callback_data = query.data or ""
    if callback_data == "route:cancel":
        context.user_data.pop(ROUTE_SESSION_KEY, None)
        await query.edit_message_text("Маршрутный расчёт отменён. Начать заново: /route")
        return
    if callback_data == "route:restart":
        context.user_data[ROUTE_SESSION_KEY] = {"step": "await_route", "lead": 24, "speed": ROUTE_DEFAULT_SPEED_KMH, "mode": "simple", "spatial_step": int(ROUTE_SPATIAL_STEP_KM)}
        await query.edit_message_text("Введите новый маршрут через → или ->. Пример: Москва -> Санкт-Петербург")
        return
    if not isinstance(state, dict) or state.get("step") != "settings":
        await query.edit_message_text("Сценарий маршрута устарел. Начните заново: /route")
        return
    state = dict(state)
    if callback_data.startswith("route:lead:"):
        state["lead"] = int(callback_data.rsplit(":", 1)[1])
    elif callback_data.startswith("route:speed:"):
        state["speed"] = int(callback_data.rsplit(":", 1)[1])
    elif callback_data.startswith("route:grid:"):
        state["spatial_step"] = validate_spatial_step(int(callback_data.rsplit(":", 1)[1]))
    elif callback_data.startswith("route:mode:"):
        state["mode"] = callback_data.rsplit(":", 1)[1]
    elif callback_data == "route:run":
        origin = _unpack_point(state["origin"])
        destination = _unpack_point(state["destination"])
        parsed = ParsedRouteRequest(
            origin_query=origin.label,
            destination_query=destination.label,
            departure_lead=int(state.get("lead", 24)),
            speed_kmh=int(state.get("speed", ROUTE_DEFAULT_SPEED_KMH)),
            mode=str(state.get("mode", "simple")),
            run=None,
            spatial_step_km=validate_spatial_step(int(state.get("spatial_step", ROUTE_SPATIAL_STEP_KM))),
            step_explicit=True,
        )
        context.user_data.pop(ROUTE_SESSION_KEY, None)
        if query.message:
            await query.edit_message_text("Параметры выбраны. Запускаю маршрутный расчёт…")
            await run_route_product(query.message, origin, destination, parsed, user=update.effective_user)
        return
    else:
        return
    context.user_data[ROUTE_SESSION_KEY] = state
    await query.edit_message_text(route_settings_text(state), reply_markup=route_settings_keyboard(state))


def register_route_handlers(application: Application, *, gfs_semaphore=None, geocode_semaphore=None) -> None:
    global _GFS_SEMAPHORE, _GEOCODE_SEMAPHORE
    _GFS_SEMAPHORE = gfs_semaphore
    _GEOCODE_SEMAPHORE = geocode_semaphore
    application.add_handler(CommandHandler("route", route_command), group=-2)
    application.add_handler(CallbackQueryHandler(route_callback, pattern=r"^route:"), group=-2)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_text_message), group=-2)
