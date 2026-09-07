from __future__ import annotations

"""Native Telegram adapter for the common WeatherNext 3 service."""

import asyncio
from threading import Lock
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from geocode import GeoPoint
from geocode_choices import search_location_candidates
from messenger.profile_service import cleanup_product_result
from messenger.runtime_resources import get_runtime_resources
from messenger.weathernext3_service import (
    DEFAULT_WN3_PARAMS,
    MAP_KINDS,
    build_weathernext3_product_result,
    normalize_wn3_params,
    parse_weathernext3_input,
    wn3_kind_title,
)
from telegram_file_send import reply_png_file
from telegram_user_state import get_active_location, get_recent_locations, remember_location
from user_location_session import match_recent_location_button, recent_location_button_label

SESSION_KEY = "weathernext3_wizard"
_RESOURCES = get_runtime_resources()
WN3_SEMAPHORE = _RESOURCES.weathernext3_semaphore
GEOCODE_SEMAPHORE = _RESOURCES.geocode_semaphore
WN3_MAX_CONCURRENT = _RESOURCES.weathernext3_limit
_INSTALLED = False

KIND_BUTTONS = (
    ("point", "🌡 Прогноз"), ("meteogram", "📊 Метеограмма"),
    ("clouds", "☁ Облачность"), ("cloud_layers", "☁ Слои"),
    ("precip_native", "🌧 Native"), ("precip_imerg", "🛰 IMERG"),
    ("precip_experimental", "🧪 Experimental"), ("combo", "🗺 Облака+осадки"),
)


def _uid(update: Update) -> int:
    return int(update.effective_user.id) if update.effective_user else 0


def _pack_point(point: Any) -> dict[str, Any]:
    return {"lat": float(point.lat), "lon": float(point.lon), "label": str(point.label), "source": str(getattr(point, "source", "telegram"))}


def _unpack_point(value: dict[str, Any]) -> GeoPoint:
    return GeoPoint(float(value["lat"]), float(value["lon"]), str(value.get("label", "точка")), str(value.get("source", "telegram")))


def _state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    value = context.user_data.get(SESSION_KEY)
    return value if isinstance(value, dict) else None


def _save(context: ContextTypes.DEFAULT_TYPE, *, point: Any | None = None, params: dict[str, Any] | None = None, step: str = "params", candidates: list[Any] | None = None) -> dict[str, Any]:
    state = {
        "step": step,
        "params": normalize_wn3_params(params or DEFAULT_WN3_PARAMS),
        "point": _pack_point(point) if point is not None else None,
        "candidates": [_pack_point(item) for item in (candidates or [])],
    }
    context.user_data[SESSION_KEY] = state
    return state


def _point_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [[KeyboardButton("📍 Моя геолокация", request_location=True)]]
    recent = get_recent_locations(user_id, 4)
    for index in range(0, len(recent), 2):
        rows.append([KeyboardButton(recent_location_button_label(item)) for item in recent[index:index + 2]])
    rows.append([KeyboardButton("✖ Отмена")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True, selective=True, input_field_placeholder="Город или координаты")


def _card_text(point: GeoPoint, params: dict[str, Any]) -> str:
    p = normalize_wn3_params(params)
    if p["kind"] == "point":
        detail = f"Срок +{p['hours']} ч"
    elif p["kind"] == "meteogram":
        detail = f"{p['days']} суток · p10/p25/p50/p75/p90"
    else:
        mode = {"animation": "анимация", "single": "одна карта", "series": "серия PNG"}[p["mode"]]
        detail = f"{mode} · +{p['from']}…+{p['to']} ч · шаг {p['step']} ч · {int(p['radius'])} км"
    return (
        "🛰 WeatherNext 3\n"
        f"📍 {point.label} · {point.lat:.4f}, {point.lon:.4f}\n"
        f"{wn3_kind_title(p['kind'])} · {detail}\n\n"
        "64-членный ансамбль · T/Td station head 0.05° · surface/maps 0.1°."
    )


def _card_keyboard(params: dict[str, Any]) -> InlineKeyboardMarkup:
    p = normalize_wn3_params(params)
    rows = [[InlineKeyboardButton("▶ Построить", callback_data="wn3:run")]]
    for index in range(0, len(KIND_BUTTONS), 2):
        row = []
        for key, label in KIND_BUTTONS[index:index + 2]:
            row.append(InlineKeyboardButton(("✓ " if key == p["kind"] else "") + label, callback_data=f"wn3:kind:{key}"))
        rows.append(row)
    if p["kind"] == "point":
        rows.append([InlineKeyboardButton(("✓ " if p["hours"] == value else "") + f"+{value}ч", callback_data=f"wn3:hours:{value}") for value in (6, 12, 24, 48)])
    elif p["kind"] == "meteogram":
        rows.append([InlineKeyboardButton(("✓ " if p["days"] == value else "") + f"{value} сут", callback_data=f"wn3:days:{value}") for value in (3, 5, 10, 15)])
    else:
        if p["mode"] == "single":
            rows.append([InlineKeyboardButton(("✓ " if p["from"] == value else "") + f"+{value}ч", callback_data=f"wn3:lead:{value}") for value in (6, 12, 24, 48)])
        else:
            rows.append([InlineKeyboardButton(("✓ " if p["to"] == value else "") + f"до +{value}", callback_data=f"wn3:to:{value}") for value in (24, 48, 72, 120)])
            rows.append([InlineKeyboardButton(("✓ " if p["step"] == value else "") + f"шаг {value}", callback_data=f"wn3:step:{value}") for value in (1, 3, 6, 12)])
        rows.append([InlineKeyboardButton(("✓ " if int(p["radius"]) == value else "") + f"{value} км", callback_data=f"wn3:radius:{value}") for value in (100, 150, 250, 400)])
        rows.append([
            InlineKeyboardButton(("✓ " if p["mode"] == "animation" else "") + "Анимация", callback_data="wn3:mode:animation"),
            InlineKeyboardButton(("✓ " if p["mode"] == "single" else "") + "Одна", callback_data="wn3:mode:single"),
            InlineKeyboardButton(("✓ " if p["mode"] == "series" else "") + "PNG", callback_data="wn3:mode:series"),
        ])
    rows.append([InlineKeyboardButton("📍 Другая точка", callback_data="wn3:point"), InlineKeyboardButton("🏠 Главное меню", callback_data="wn3:home")])
    return InlineKeyboardMarkup(rows)


async def _show_card(message, context: ContextTypes.DEFAULT_TYPE, point: GeoPoint, params: dict[str, Any]) -> None:
    state = _save(context, point=point, params=params)
    await message.reply_text(_card_text(point, state["params"]), reply_markup=_card_keyboard(state["params"]))


async def _ask_point(message, context: ContextTypes.DEFAULT_TYPE, params: dict[str, Any]) -> None:
    _save(context, params=params, step="await_point")
    await message.reply_text("🛰 WeatherNext 3\nУкажите город, координаты или отправьте геолокацию.", reply_markup=_point_keyboard(_uid_from_message(message)))


def _uid_from_message(message) -> int:
    user = getattr(message, "from_user", None)
    return int(getattr(user, "id", 0) or 0)


async def _resolve_point(message, context: ContextTypes.DEFAULT_TYPE, query: str, params: dict[str, Any], *, direct: bool = False) -> None:
    user_id = _uid_from_message(message)
    recent = match_recent_location_button(user_id, query)
    if recent is not None:
        if direct:
            await _run(message, context, recent, params)
        else:
            await _show_card(message, context, recent, params)
        return
    async with GEOCODE_SEMAPHORE:
        candidates = await asyncio.to_thread(search_location_candidates, query, 5)
    if not candidates:
        await message.reply_text("Точка не найдена. Уточните город или используйте координаты.")
        return
    if len(candidates) > 1:
        state = _save(context, params=params, step="choose_place", candidates=candidates[:5])
        state["direct"] = bool(direct)
        rows = [[InlineKeyboardButton(str(item.label)[:58], callback_data=f"wn3:place:{index}")] for index, item in enumerate(candidates[:5])]
        rows.append([InlineKeyboardButton("Отмена", callback_data="wn3:cancel")])
        await message.reply_text("Найдено несколько точек. Выберите нужную:", reply_markup=InlineKeyboardMarkup(rows))
        return
    point = candidates[0]
    if direct:
        await _run(message, context, point, params)
    else:
        await _show_card(message, context, point, params)


async def _send_result(message, result) -> None:
    for attachment in result.attachments:
        path = Path(attachment.path)
        if attachment.kind == "image":
            await reply_png_file(message, path, caption=attachment.caption, prefer_photo=True)
        elif attachment.kind == "animation":
            with path.open("rb") as handle:
                await message.reply_animation(animation=handle, caption=attachment.caption)
        else:
            with path.open("rb") as handle:
                await message.reply_document(document=handle, caption=attachment.caption, filename=attachment.filename)


async def _run(message, context: ContextTypes.DEFAULT_TYPE, point: GeoPoint, params: dict[str, Any]) -> None:
    p = normalize_wn3_params(params)
    _save(context, point=point, params=p)
    status = await message.reply_text(f"⏳ WeatherNext 3 · {wn3_kind_title(p['kind'])}\n📍 {point.label}\nПроверяю опубликованный init…", reply_markup=ReplyKeyboardRemove())
    result = None
    snapshot = {"event": None}
    lock = Lock()
    stopped = False

    def progress(event) -> None:
        with lock:
            snapshot["event"] = event

    async def reporter() -> None:
        last = ""
        while not stopped:
            with lock:
                event = snapshot["event"]
            if event is not None:
                if event.stage in {"check", "fetch_start"}:
                    body = "1/4 Проверяю опубликованный init…"
                elif event.stage in {"fetch", "format"}:
                    body = f"2/4 {event.message or 'Получаю BigQuery данные'}…"
                elif event.stage in {"render", "plot_start", "plot_frame"}:
                    body = f"3/4 Рисую кадры {event.current}/{event.total}…" if event.current and event.total else f"3/4 {event.message or 'Рисую продукт'}…"
                elif event.stage == "encode":
                    body = "4/4 Кодирую анимацию…"
                else:
                    body = event.message or "Выполняю расчёт…"
                text = f"⏳ WeatherNext 3 · {wn3_kind_title(p['kind'])}\n📍 {point.label}\n{body}"
                if text != last:
                    try:
                        await status.edit_text(text)
                        last = text
                    except Exception:
                        pass
            await asyncio.sleep(1.0)

    task = asyncio.create_task(reporter())
    try:
        async with WN3_SEMAPHORE:
            result = await asyncio.to_thread(
                build_weathernext3_product_result,
                point,
                p["kind"],
                progress_callback=progress,
                **{key: value for key, value in p.items() if key != "kind"},
            )
        stopped = True
        await task
        await status.edit_text(result.summary)
        await _send_result(message, result)
        if result.repeat_command:
            await message.reply_text(f"📋 Повторить:\n{result.repeat_command}")
        remember_location(_uid_from_message(message), point, activate=True)
    except Exception as exc:
        stopped = True
        await task
        await status.edit_text(f"Ошибка WeatherNext 3: {exc}")
    finally:
        if result is not None:
            cleanup_product_result(result)


async def wn3_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    args = " ".join(context.args or []).strip()
    if not args:
        active = get_active_location(_uid(update))
        if active is not None:
            await _show_card(message, context, GeoPoint(active.lat, active.lon, active.label, active.source), dict(DEFAULT_WN3_PARAMS))
        else:
            await _ask_point(message, context, dict(DEFAULT_WN3_PARAMS))
        raise ApplicationHandlerStop
    active = get_active_location(_uid(update))
    if active is not None and (args.startswith("+") or any(args.startswith(prefix) for prefix in ("kind=", "days=", "hours=", "lead=", "from=", "to=", "step=", "radius=", "mode=", "basemap=", "base="))):
        parsed = parse_weathernext3_input(f"0 0 {args}")
        await _run(message, context, GeoPoint(active.lat, active.lon, active.label, active.source), parsed.params)
        raise ApplicationHandlerStop
    try:
        parsed = parse_weathernext3_input(args)
    except Exception as exc:
        await message.reply_text(f"Ошибка WeatherNext 3: {exc}")
        raise ApplicationHandlerStop
    await _resolve_point(message, context, parsed.location_query, parsed.params, direct=parsed.direct_run)
    raise ApplicationHandlerStop


async def wn3_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    data = query.data or ""
    await query.answer()
    message = query.message
    if message is None:
        return
    if data == "home:wn3":
        active = get_active_location(_uid(update))
        if active is not None:
            await _show_card(message, context, GeoPoint(active.lat, active.lon, active.label, active.source), dict(DEFAULT_WN3_PARAMS))
        else:
            await _ask_point(message, context, dict(DEFAULT_WN3_PARAMS))
        raise ApplicationHandlerStop
    state = _state(context)
    if state is None:
        await message.reply_text("Сценарий WeatherNext 3 устарел. Запустите /wn3.")
        raise ApplicationHandlerStop
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""
    if action in {"cancel", "home"}:
        context.user_data.pop(SESSION_KEY, None)
        if action == "home":
            import telegram_personal_ux
            await telegram_personal_ux._show_home(message, _uid(update))
        else:
            await message.reply_text("WeatherNext 3: выбор отменён.", reply_markup=ReplyKeyboardRemove())
        raise ApplicationHandlerStop
    if action == "place":
        try:
            point = _unpack_point(state.get("candidates", [])[int(value)])
        except (ValueError, IndexError, KeyError):
            await message.reply_text("Вариант точки устарел. Запустите /wn3 заново.")
            raise ApplicationHandlerStop
        if state.pop("direct", False):
            await _run(message, context, point, state["params"])
        else:
            await _show_card(message, context, point, state["params"])
        raise ApplicationHandlerStop
    if action == "point":
        await _ask_point(message, context, state["params"])
        raise ApplicationHandlerStop
    if not state.get("point"):
        await message.reply_text("Точка WeatherNext 3 потеряна. Запустите /wn3 заново.")
        raise ApplicationHandlerStop
    point = _unpack_point(state["point"])
    p = normalize_wn3_params(state["params"])
    if action == "kind":
        p["kind"] = value
    elif action in {"hours", "days", "to", "step", "radius"}:
        p[action] = int(value)
    elif action == "lead":
        p["from"] = p["to"] = int(value)
        p["mode"] = "single"
    elif action == "mode":
        p["mode"] = value
        if value == "single":
            p["from"] = p["to"] = min(max(1, int(p["hours"])), 48)
        elif p["to"] <= p["from"]:
            p["from"], p["to"] = 1, 48
    elif action == "run":
        await _run(message, context, point, p)
        raise ApplicationHandlerStop
    else:
        await message.reply_text("Кнопка WeatherNext 3 устарела. Запустите /wn3.")
        raise ApplicationHandlerStop
    await _show_card(message, context, point, p)
    raise ApplicationHandlerStop


async def wn3_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    if state is None or state.get("step") != "await_point" or update.effective_message is None or update.effective_message.location is None:
        return
    loc = update.effective_message.location
    point = GeoPoint(float(loc.latitude), float(loc.longitude), f"геолокация {loc.latitude:.4f}, {loc.longitude:.4f}", "telegram")
    await _show_card(update.effective_message, context, point, state["params"])
    raise ApplicationHandlerStop


async def wn3_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _state(context)
    message = update.effective_message
    if state is None or state.get("step") != "await_point" or message is None or not message.text:
        return
    if message.text.strip() == "✖ Отмена":
        context.user_data.pop(SESSION_KEY, None)
        await message.reply_text("WeatherNext 3: выбор отменён.", reply_markup=ReplyKeyboardRemove())
        raise ApplicationHandlerStop
    await _resolve_point(message, context, message.text.strip(), state["params"])
    raise ApplicationHandlerStop


def _insert_wn3_button(keyboard: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = [list(row) for row in keyboard.inline_keyboard]
    if any(button.callback_data == "home:wn3" for row in rows for button in row):
        return keyboard
    insert_at = max(0, len(rows) - 1)
    rows.insert(insert_at, [InlineKeyboardButton("🛰 WeatherNext 3", callback_data="home:wn3")])
    return InlineKeyboardMarkup(rows)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    import telegram_concise_ux
    import telegram_personal_ux

    concise_keyboard = telegram_concise_ux.home_keyboard
    concise_text = telegram_concise_ux.home_text
    personal_keyboard = telegram_personal_ux.home_keyboard
    personal_text = telegram_personal_ux.home_text

    def patched_concise_keyboard():
        return _insert_wn3_button(concise_keyboard())

    def patched_concise_text():
        return concise_text().replace("🌦 GFS-прогнозы", "🌦 Модельные прогнозы GFS + WeatherNext 3")

    def patched_personal_keyboard(user_id: int):
        return _insert_wn3_button(personal_keyboard(user_id))

    def patched_personal_text(user_id: int):
        return personal_text(user_id).replace("🌦 GFS 0.25 · модельные прогнозы", "🌦 GFS 0.25 + WeatherNext 3 · модельные прогнозы")

    telegram_concise_ux.home_keyboard = patched_concise_keyboard
    telegram_concise_ux.home_text = patched_concise_text
    telegram_personal_ux.home_keyboard = patched_personal_keyboard
    telegram_personal_ux.home_text = patched_personal_text


def register(application) -> None:
    # Negative group makes WN3 callbacks win before generic home/product handlers.
    application.add_handler(CommandHandler("wn3", wn3_command), group=-10)
    application.add_handler(CallbackQueryHandler(wn3_callback, pattern=r"^(?:home:wn3|wn3:.*)$"), group=-10)
    application.add_handler(MessageHandler(filters.LOCATION, wn3_location), group=-10)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wn3_text), group=-10)
