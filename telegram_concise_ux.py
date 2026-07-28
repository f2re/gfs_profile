from __future__ import annotations

"""Compact Telegram copy and stage-aware keyboards.

The reply keyboard with location sharing exists only while the bot is waiting
for a point. Home and parameter navigation use inline keyboards. Scientific
disclaimers remain in final products instead of being repeated in every prompt.
"""

import asyncio
import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from telegram_product_wizard import PRODUCT_WIZARD_KEY, copy_command
from user_location_session import RECENT_LOCATION_PREFIX, recent_location_button_label

CANCEL_TEXT = "✖ Отмена"


def home_text() -> str:
    return (
        "🌦 GFS-прогнозы по точке и маршруту\n\n"
        "/profile — вертикальный профиль\n"
        "/route — разрез вдоль маршрута\n"
        "/aero, /skewt — аэродиаграммы\n"
        "/windgram — ветер, температура или влажность по срокам\n"
        "/cloudgram — облака, осадки и грозы\n"
        "/map — карта, серия или анимация\n\n"
        "Выберите продукт."
    )


def help_text() -> str:
    return (
        "Как пользоваться\n\n"
        "1. Выберите продукт.\n"
        "2. Укажите город, координаты или геолокацию.\n"
        "3. Настройте срок и параметры кнопками.\n\n"
        "Быстрые примеры:\n"
        "<code>/profile Москва +24</code>\n"
        "<code>/route Москва -&gt; Санкт-Петербург +6</code>\n"
        "<code>/cloudgram Москва to=72 mode=simple</code>\n"
        "<code>/map Москва from=0 to=24 step=3 mode=gif</code>\n\n"
        "/cycle — последний цикл · /status — доступность · /cancel — сброс"
    )


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📈 Профиль", callback_data="home:profile"),
                InlineKeyboardButton("✈️ Маршрут", callback_data="home:route"),
            ],
            [
                InlineKeyboardButton("☁️ Облака", callback_data="home:cloudgram"),
                InlineKeyboardButton("🟦 Срок × уровень", callback_data="home:windgram"),
            ],
            [
                InlineKeyboardButton("🧾 Аэродиаграмма", callback_data="home:aero"),
                InlineKeyboardButton("🗺️ Карта", callback_data="home:map"),
            ],
            [InlineKeyboardButton("❓ Как пользоваться", callback_data="home:help")],
        ]
    )


def point_keyboard(recent_locations: list[Any] | None = None) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [[KeyboardButton("📍 Моя геолокация", request_location=True)]]
    buttons = [KeyboardButton(recent_location_button_label(point)) for point in (recent_locations or [])[:4]]
    for index in range(0, len(buttons), 2):
        rows.append(buttons[index : index + 2])
    rows.append([KeyboardButton(CANCEL_TEXT)])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
        is_persistent=False,
        input_field_placeholder="Город или координаты",
    )


def point_prompt_text(state: dict[str, object]) -> str:
    product = {
        "aero": "🧾 Аэродиаграмма",
        "windgram": "🟦 Срок × уровень",
        "cloudgram": "☁️ Облака и осадки",
        "map": "🗺️ Карта",
    }.get(str(state.get("product", "")), "Продукт GFS")
    return f"{product}\n\nУкажите город, координаты или отправьте геолокацию."


def _point_line(state: dict[str, object]) -> str:
    point = state.get("point")
    if not isinstance(point, dict):
        return "📍 Точка не выбрана"
    label = " ".join(str(point.get("label", "точка")).split())
    return f"📍 {label} · {float(point.get('lat', 0.0)):.4f}, {float(point.get('lon', 0.0)):.4f}"


def params_text(state: dict[str, object]) -> str:
    product = str(state.get("product", ""))
    point = _point_line(state)
    command = copy_command(state)
    command_line = f"\n<code>{html.escape(command)}</code>" if command else ""

    if product == "aero":
        body = f"🧾 Аэродиаграмма\n{point}\nТип: {str(state.get('diagram_type', 'stuve')).upper()} · срок +{int(state.get('lead', 24))} ч"
    elif product == "windgram":
        param = {"wind": "ветер", "temp": "температура", "rh": "влажность"}.get(str(state.get("param", "wind")), "ветер")
        body = (
            f"🟦 Срок × уровень\n{point}\n"
            f"{param} · +{int(state.get('from', 0))}…+{int(state.get('to', 120))} ч · "
            f"шаг {int(state.get('time_step', 6))} ч · до {int(state.get('top', 500))} гПа"
        )
    elif product == "cloudgram":
        mode = "упрощённый" if str(state.get("mode", "pro")) == "simple" else "профессиональный"
        body = (
            f"☁️ Облака и осадки\n{point}\n"
            f"{mode} · +{int(state.get('from', 0))}…+{int(state.get('to', 72))} ч · шаг {int(state.get('time_step', 3))} ч"
        )
    elif product == "map":
        mode = str(state.get("mode", "single"))
        if mode == "single":
            period = f"срок +{int(state.get('lead', 24))} ч"
            mode_label = "одна карта"
        else:
            period = f"+{int(state.get('from', 0))}…+{int(state.get('to', 24))} ч · шаг {int(state.get('time_step', 6))} ч"
            mode_label = "анимация" if mode == "gif" else "серия PNG"
        body = f"🗺️ Карта\n{point}\n{mode_label} · {period} · радиус {int(state.get('radius', 100))} км"
    else:
        body = f"Параметры\n{point}"
    return body + command_line


async def _remove_reply_keyboard(message) -> None:
    """Remove a previous reply keyboard without leaving a visible service row."""

    if message is None:
        return
    try:
        marker = await message.reply_text("\u2063", reply_markup=ReplyKeyboardRemove())
        await asyncio.sleep(0)
        try:
            await marker.delete()
        except Exception:
            pass
    except Exception:
        pass


def _patch_product_messages() -> None:
    import telegram_aero as aero
    import telegram_cloudgram as cloud
    import telegram_map as map_module
    import telegram_windgram as wind

    if getattr(map_module, "_CONCISE_COPY_PATCHED", False):
        return

    def repeat_aero(point, parsed, run) -> str:
        return f"📋 <code>{html.escape(aero.repeat_aero_command(point, parsed, run))}</code>"

    def repeat_wind(point, parsed, run) -> str:
        return f"📋 <code>{html.escape(wind.repeat_windgram_command(point, parsed, run))}</code>"

    def wind_caption(data) -> str:
        step = data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0
        name = wind.PARAM_NAMES.get(data.param, data.param)
        return (
            f"🟦 Windgram · {name}\n"
            f"{data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · шаг {step} ч\n"
            f"Узел {data.grid_lat:.3f}, {data.grid_lon:.3f}"
        )

    def repeat_cloud(point, parsed, run) -> str:
        return f"📋 <code>{html.escape(cloud.repeat_cloudgram_command(point, parsed, run))}</code>"

    def cloud_caption(data, mode="pro") -> str:
        step = data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0
        maximum = max((cell.hazard_score for cell in data.cells), default=0)
        missing = f"\nНет полей: {', '.join(data.missing_fields)}" if data.missing_fields else ""
        return (
            f"☁️ Cloudgram · {'SIMPLE' if mode == 'simple' else 'PRO'}\n"
            f"{data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · шаг {step} ч\n"
            f"Максимальная оценка: {cloud._hazard_label(maximum)}{missing}"
        )

    def repeat_map(point, parsed, run) -> str:
        return f"📋 <code>{html.escape(map_module._repeat_command(point, parsed, run))}</code>"

    def map_status(data: dict, *, animated: bool = False, series: bool = False, lead_count: int = 1) -> str:
        run = data["run"]
        point = data["point"]
        title = "🗺️ Карта"
        if animated:
            title = f"🗺️ Анимация · {lead_count} кадров"
        elif series:
            title = f"🗺️ Серия карт · {lead_count} кадров"
        return f"{title}\n📍 {point.label}\n🕒 {run.date} {run.cycle}Z · +{data['lead_hour']} ч"

    def map_file_caption(data: dict, *, animated: bool = False, series: bool = False, animation_format: str = "MP4-анимация") -> str:
        run = data["run"]
        point = data["point"]
        kind = animation_format if animated else "PNG-серия" if series else "PNG"
        lines = [
            f"{kind} · MAP · {run.date} {run.cycle}Z",
            f"{point.label} · +{int(data['lead_hour'])} ч · радиус {int(data['radius_km'])} км",
        ]
        missing = data.get("missing") or set()
        if missing:
            lines.append("Нет полей: " + ", ".join(sorted(missing)))
        return "\n".join(lines)

    aero.format_repeat_aero_message = repeat_aero
    wind.format_repeat_windgram_message = repeat_wind
    wind.format_windgram_caption = wind_caption
    cloud.format_repeat_cloudgram_message = repeat_cloud
    cloud.format_cloudgram_caption = cloud_caption
    map_module.format_repeat_map_message = repeat_map
    map_module.format_map_status = map_status
    map_module.format_map_file_caption = map_file_caption
    map_module._CONCISE_COPY_PATCHED = True


def _patch_route_messages() -> None:
    import telegram_route as route

    if getattr(route, "_CONCISE_COPY_PATCHED", False):
        return
    original_route_command = route.route_command

    async def route_command(update, context) -> None:
        raw = " ".join(context.args or []).strip()
        if raw:
            await _remove_reply_keyboard(update.effective_message)
            await original_route_command(update, context)
            return
        route.record_telegram_user(update.effective_user)
        message = update.effective_message
        if not message:
            return
        context.user_data[route.ROUTE_SESSION_KEY] = {
            "step": "await_route",
            "lead": 24,
            "speed": route.ROUTE_DEFAULT_SPEED_KMH,
            "mode": "simple",
            "spatial_step": int(route.ROUTE_SPATIAL_STEP_KM),
        }
        await message.reply_text(
            "✈️ Маршрутный профиль\n\n"
            "Введите начало и конец:\n"
            "<code>Москва -&gt; Санкт-Петербург</code>\n\n"
            "Далее выберите срок, скорость, детализацию и режим.",
            parse_mode=ParseMode.HTML,
        )

    def route_settings_text(state: dict[str, object]) -> str:
        origin = route._unpack_point(state["origin"])
        destination = route._unpack_point(state["destination"])
        speed = int(state.get("speed", route.ROUTE_DEFAULT_SPEED_KMH))
        lead = int(state.get("lead", 24))
        mode = "профи" if str(state.get("mode", "simple")) == "pro" else "упрощённый"
        grid = route.validate_spatial_step(int(state.get("spatial_step", route.ROUTE_SPATIAL_STEP_KM)))
        distance, duration, specs = route._route_plan(origin, destination, lead, speed, grid)
        max_lead = max(item[4] for item in specs)
        warning = "\n⚠️ Долгий расчёт: выберите 50 или 100 км." if len(specs) >= route.ROUTE_LONG_POINT_WARNING else ""
        return (
            f"✈️ {origin.label} → {destination.label}\n"
            f"{distance:.0f} км · {duration:.1f} ч · +{lead}…+{max_lead} ч\n"
            f"{speed} км/ч · сетка {grid} км ({len(specs)} точек) · {mode}{warning}"
        )

    route.route_command = route_command
    route.route_settings_text = route_settings_text
    route._CONCISE_COPY_PATCHED = True


def install(namespace: dict[str, Any]) -> None:
    """Patch core handlers after telegram_bot_core is embedded."""

    if namespace.get("_CONCISE_UX_INSTALLED"):
        return
    _patch_product_messages()
    _patch_route_messages()

    original_aero_command = namespace["aero_command"]
    original_skewt_command = namespace["skewt_command"]
    original_windgram_command = namespace["windgram_command"]
    original_cloudgram_command = namespace["cloudgram_command"]
    original_map_command = namespace["map_command"]
    original_product_callback = namespace["product_wizard_callback"]

    def location_keyboard_for_user(user_id: int):
        return point_keyboard(namespace["get_recent_locations"](user_id))

    async def show_home(message, prefix: str | None = None) -> None:
        await _remove_reply_keyboard(message)
        text = f"{prefix}\n\n{home_text()}" if prefix else home_text()
        await message.reply_text(text, reply_markup=home_keyboard())

    async def show_profile_leads(message, context, point, run=None) -> None:
        namespace["_set_pending_point"](context, point, run)
        await _remove_reply_keyboard(message)
        await message.reply_text(
            f"📍 {point.label}\nВыберите срок.",
            reply_markup=namespace["lead_keyboard"](0),
        )

    async def resolve_wizard_point(message, context, raw: str) -> bool:
        state = namespace["_wizard_state"](context)
        if not state or state.get("step") not in {"await_point", "choose_place"}:
            return False
        state = dict(state)
        state["step"] = "await_point"
        user_id = namespace["_user_id_from_message"](message)
        recent = namespace["match_recent_location_button"](user_id, raw)
        if recent is not None:
            namespace["remember_location"](user_id, recent)
            await show_wizard_params(message, context, namespace["wizard_set_point"](state, namespace["_pack_point"](recent)))
            return True
        if raw.startswith(RECENT_LOCATION_PREFIX):
            await message.reply_text("Последняя точка недоступна. Укажите её заново.", reply_markup=location_keyboard_for_user(user_id))
            return True
        try:
            async with namespace["GEOCODE_SEMAPHORE"]:
                candidates = await asyncio.to_thread(namespace["search_location_candidates"], raw, 5)
        except (namespace["GeocodeError"], ValueError, namespace["GfsProfileError"]) as exc:
            await message.reply_text(f"Не удалось найти точку: {exc}", reply_markup=location_keyboard_for_user(user_id))
            return True
        if not candidates:
            await message.reply_text("Точка не найдена. Уточните город или координаты.", reply_markup=location_keyboard_for_user(user_id))
            return True
        if len(candidates) > 1:
            state["candidates"] = [namespace["_pack_point"](point) for point in candidates[:5]]
            state["step"] = "choose_place"
            context.user_data[PRODUCT_WIZARD_KEY] = state
            await _remove_reply_keyboard(message)
            await message.reply_text("Выберите точку:", reply_markup=namespace["wizard_place_keyboard"]([point.label for point in candidates[:5]]))
            return True
        point = candidates[0]
        namespace["remember_location"](user_id, point)
        await show_wizard_params(message, context, namespace["wizard_set_point"](state, namespace["_pack_point"](point)))
        return True

    async def resolve_profile_request(message, context, raw: str) -> None:
        user_id = namespace["_user_id_from_message"](message)
        try:
            parsed = namespace["parse_request"](raw)
            async with namespace["GEOCODE_SEMAPHORE"]:
                candidates = await asyncio.to_thread(namespace["search_location_candidates"], parsed.location_query, 3)
        except (namespace["GeocodeError"], ValueError, namespace["GfsProfileError"]) as exc:
            await message.reply_text(f"Не удалось найти точку: {exc}", reply_markup=location_keyboard_for_user(user_id))
            return
        if not candidates:
            await message.reply_text("Точка не найдена. Уточните город или координаты.", reply_markup=location_keyboard_for_user(user_id))
            return
        if len(candidates) > 1:
            context.user_data["pending_candidates"] = {
                "candidates": [namespace["_pack_point"](point) for point in candidates[:3]],
                "lead_hour": parsed.lead_hour,
                "run": namespace["_pack_run"](parsed.run),
                "lead_from_user": parsed.lead_from_user,
            }
            await _remove_reply_keyboard(message)
            await message.reply_text("Выберите точку:", reply_markup=namespace["place_keyboard"]([point.label for point in candidates[:3]]))
            return
        point = candidates[0]
        namespace["remember_location"](user_id, point)
        if parsed.lead_from_user:
            await _remove_reply_keyboard(message)
            await namespace["_tracked_run_profile"](message, point, parsed.lead_hour, parsed.run, request_text=f"/profile {raw}")
            return
        await show_profile_leads(message, context, point, parsed.run)

    async def start(update, context) -> None:
        message = update.effective_message
        if not message:
            return
        namespace["_clear_pending"](context)
        context.user_data.pop("route_profile_wizard", None)
        await show_home(message)

    async def help_command(update, context) -> None:
        message = update.effective_message
        if not message:
            return
        await _remove_reply_keyboard(message)
        await message.reply_text(help_text(), parse_mode=ParseMode.HTML, reply_markup=home_keyboard())

    async def cancel_command(update, context) -> None:
        namespace["_clear_pending"](context)
        context.user_data.pop("route_profile_wizard", None)
        message = update.effective_message
        if message:
            await show_home(message, "Выбор сброшен.")

    async def start_product_wizard(message, context, state) -> None:
        namespace["_clear_pending"](context)
        context.user_data[PRODUCT_WIZARD_KEY] = state
        await message.reply_text(
            point_prompt_text(state),
            reply_markup=location_keyboard_for_user(namespace["_user_id_from_message"](message)),
        )

    async def show_wizard_params(message, context, state) -> None:
        context.user_data[PRODUCT_WIZARD_KEY] = state
        await _remove_reply_keyboard(message)
        await message.reply_text(
            params_text(state),
            parse_mode=ParseMode.HTML,
            reply_markup=namespace["params_keyboard"](state),
        )

    async def profile_command(update, context) -> None:
        message = update.effective_message
        if not message:
            return
        raw = " ".join(context.args or []).strip()
        if raw:
            await resolve_profile_request(message, context, raw)
            return
        namespace["_clear_pending"](context)
        await message.reply_text(
            "📈 Вертикальный профиль\n\nУкажите город, координаты или отправьте геолокацию.",
            reply_markup=location_keyboard_for_user(namespace["_user_id_from_update"](update)),
        )

    def wrap_product_command(original):
        async def wrapped(update, context) -> None:
            raw = " ".join(context.args or []).strip()
            if raw:
                await _remove_reply_keyboard(update.effective_message)
            await original(update, context)

        return wrapped

    async def text_message(update, context) -> None:
        message = update.effective_message
        if not message or not message.text:
            return
        text = message.text.strip()
        if text == CANCEL_TEXT:
            await cancel_command(update, context)
            return
        if text in {"❓ Помощь", "Помощь", "help"}:
            await help_command(update, context)
            return
        if await resolve_wizard_point(message, context, text):
            return
        user_id = namespace["_user_id_from_update"](update)
        recent = namespace["match_recent_location_button"](user_id, text)
        if recent is not None:
            namespace["remember_location"](user_id, recent)
            await show_profile_leads(message, context, recent)
            return
        if text.startswith(RECENT_LOCATION_PREFIX):
            await message.reply_text("Последняя точка недоступна. Укажите её заново.", reply_markup=location_keyboard_for_user(user_id))
            return
        await resolve_profile_request(message, context, text)

    async def location_message(update, context) -> None:
        message = update.effective_message
        if not message or not message.location:
            return
        point = namespace["GeoPoint"](message.location.latitude, message.location.longitude, "Текущая геолокация", "telegram")
        user_id = namespace["_user_id_from_update"](update)
        namespace["remember_location"](user_id, point)
        state = namespace["_wizard_state"](context)
        if state and state.get("step") in {"await_point", "choose_place"}:
            await show_wizard_params(message, context, namespace["wizard_set_point"](state, namespace["_pack_point"](point)))
            return
        await show_profile_leads(message, context, point)

    async def place_callback(update, context) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        data = query.data or ""
        if data == "cancel":
            context.user_data.pop("pending_candidates", None)
            namespace["_clear_pending"](context)
            await query.edit_message_text(home_text(), reply_markup=home_keyboard())
            return
        pending = context.user_data.get("pending_candidates")
        if not isinstance(pending, dict):
            await query.edit_message_text("Выбор устарел.", reply_markup=home_keyboard())
            return
        index = int(data.split(":", 1)[1])
        candidates = pending.get("candidates", [])
        if index < 0 or index >= len(candidates):
            await query.edit_message_text("Выбор устарел.", reply_markup=home_keyboard())
            return
        point = namespace["_unpack_point"](candidates[index])
        namespace["remember_location"](namespace["_user_id_from_update"](update), point)
        run = namespace["_unpack_run"](pending.get("run"))
        lead = int(pending.get("lead_hour", namespace["DEFAULT_LEAD"]))
        lead_from_user = bool(pending.get("lead_from_user", False))
        context.user_data.pop("pending_candidates", None)
        if not query.message:
            return
        if lead_from_user:
            await query.edit_message_text(f"📍 {point.label}\nЗапускаю профиль +{lead} ч…")
            await namespace["_tracked_run_profile"](query.message, point, lead, run, user=update.effective_user)
            return
        namespace["_set_pending_point"](context, point, run)
        await query.edit_message_text(f"📍 {point.label}\nВыберите срок.", reply_markup=namespace["lead_keyboard"](0))

    async def product_wizard_callback(update, context) -> None:
        query = update.callback_query
        data = (query.data or "") if query else ""
        if query and data == "wiz:cancel":
            await query.answer()
            context.user_data.pop(PRODUCT_WIZARD_KEY, None)
            await query.edit_message_text(home_text(), reply_markup=home_keyboard())
            raise ApplicationHandlerStop
        if query and data == "wiz:point":
            await query.answer()
            state = namespace["_wizard_state"](context)
            if not state:
                await query.edit_message_text(home_text(), reply_markup=home_keyboard())
                return
            state = dict(state)
            state["step"] = "await_point"
            state.pop("point", None)
            state.pop("candidates", None)
            context.user_data[PRODUCT_WIZARD_KEY] = state
            await query.edit_message_text("Выберите другую точку.")
            if query.message:
                await query.message.reply_text(
                    point_prompt_text(state),
                    reply_markup=location_keyboard_for_user(namespace["_user_id_from_update"](update)),
                )
            raise ApplicationHandlerStop
        await original_product_callback(update, context)

    namespace.update(
        {
            "_CONCISE_UX_INSTALLED": True,
            "_location_keyboard_for_user": location_keyboard_for_user,
            "point_prompt_text": point_prompt_text,
            "params_text": params_text,
            "_profile_repeat_message": lambda point, lead, run: f"📋 <code>{html.escape(namespace['_profile_command'](point, lead, run))}</code>",
            "_resolve_wizard_point": resolve_wizard_point,
            "resolve_profile_request": resolve_profile_request,
            "start": start,
            "help_command": help_command,
            "cancel_command": cancel_command,
            "_start_product_wizard": start_product_wizard,
            "_show_wizard_params": show_wizard_params,
            "profile_command": profile_command,
            "aero_command": wrap_product_command(original_aero_command),
            "skewt_command": wrap_product_command(original_skewt_command),
            "windgram_command": wrap_product_command(original_windgram_command),
            "cloudgram_command": wrap_product_command(original_cloudgram_command),
            "map_command": wrap_product_command(original_map_command),
            "text_message": text_message,
            "location_message": location_message,
            "place_callback": place_callback,
            "product_wizard_callback": product_wizard_callback,
        }
    )


def register(application, namespace: dict[str, Any]) -> None:
    async def home_callback(update, context) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        action = (query.data or "").split(":", 1)[-1]
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if action == "help":
            await namespace["help_command"](update, context)
            return

        handlers = {
            "profile": namespace["profile_command"],
            "aero": namespace["aero_command"],
            "windgram": namespace["windgram_command"],
            "cloudgram": namespace["cloudgram_command"],
            "map": namespace["map_command"],
        }
        if action == "route":
            import telegram_route

            handler = telegram_route.route_command
        else:
            handler = handlers.get(action)
        if handler is None:
            return

        old_args = getattr(context, "args", None)
        context.args = []
        try:
            await handler(update, context)
        finally:
            context.args = old_args

    application.add_handler(CallbackQueryHandler(home_callback, pattern=r"^home:"), group=-2)
