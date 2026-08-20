from __future__ import annotations

"""User-oriented Telegram flow with persistent defaults and quick actions.

The module is installed as a compatibility layer around the existing handlers.
It deliberately leaves the bot single-process and keeps transient wizard state
in ``context.user_data``.  Only validated points and product settings are stored
in SQLite by :mod:`telegram_user_state`.
"""

import html
import re
from contextvars import ContextVar
from typing import Any, Mapping

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler

from geocode import GeoPoint
from telegram_product_wizard import PRODUCT_WIZARD_KEY
from telegram_user_state import (
    ProductPreference,
    clear_locations,
    clear_product_preference,
    clear_user_data,
    default_product_params,
    get_active_location,
    get_last_success_preference,
    get_product_preference,
    get_quick_preferences,
    get_recent_locations,
    normalise_product_params,
    normalise_point,
    record_product_success,
    save_product_selection,
    set_active_location,
)
from user_location_session import remember_location_without_activation

_PENDING_WIZARD_STATE: ContextVar[dict[str, Any] | None] = ContextVar(
    "telegram_personal_pending_wizard_state",
    default=None,
)
_INSTALLED = False

PRODUCT_LABELS = {
    "profile": "📈 Профиль",
    "aero": "🧾 Аэродиаграмма",
    "windgram": "🟦 Срок × уровень",
    "cloudgram": "☁️ Облака",
    "map": "🗺 Карта",
    "meteogram": "📊 Метеограмма",
    "route": "✈️ Маршрут",
}

_PARAM_RE = re.compile(r"(?<!\S)(?P<key>[a-z_]+)=(?P<value>[^\s]+)", re.IGNORECASE)
_LEAD_RE = re.compile(r"(?:^|\s)\+(?P<value>\d{1,3})(?=\s|$)")
_MAP_TIME_RE = re.compile(
    r"(?:^|\s)(?:\+\d{1,3}|lead=|from=|to=|step=|mode=|anim=)",
    re.IGNORECASE,
)


def _user_id_from_message(message) -> int:
    user = getattr(message, "from_user", None)
    return int(getattr(user, "id", 0) or 0)


def _user_id_from_update(update) -> int:
    user = getattr(update, "effective_user", None)
    return int(getattr(user, "id", 0) or 0)


def _is_scheduled_message(message: Any) -> bool:
    return message.__class__.__name__ == "ScheduledMessage"


def _pack_point(point: Mapping[str, Any] | Any | None) -> dict[str, Any] | None:
    return normalise_point(point)


def _unpack_point(value: Mapping[str, Any]) -> GeoPoint:
    return GeoPoint(
        float(value["lat"]),
        float(value["lon"]),
        str(value.get("label", "точка")),
        str(value.get("source", "saved")),
    )


def _preference_point(user_id: int, preference: ProductPreference | None) -> dict[str, Any] | None:
    if preference and preference.point:
        return dict(preference.point)
    active = get_active_location(user_id)
    return _pack_point(active)


def _state_params(state: Mapping[str, Any]) -> dict[str, Any]:
    product = str(state.get("product", ""))
    payload = dict(state)
    for key in (
        "product",
        "point",
        "step",
        "candidates",
        "_schedule_setup",
    ):
        payload.pop(key, None)
    if product == "map":
        variants = payload.pop("_map_variants", None)
        if isinstance(variants, Mapping):
            payload["variants"] = dict(variants)
    return normalise_product_params(product, payload)


def _map_state_params(params: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(params)
    variants = value.pop("variants", None)
    if isinstance(variants, Mapping):
        value["_map_variants"] = {
            str(key): dict(item)
            for key, item in variants.items()
            if isinstance(item, Mapping)
        }
    return value


def _state_for_user(user_id: int, state: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(state)
    product = str(result.get("product", ""))
    preference = get_product_preference(user_id, product)
    params = preference.params if preference else default_product_params(product)
    result.update(_map_state_params(params) if product == "map" else params)
    point = _preference_point(user_id, preference)
    if point:
        result["point"] = point
        result["step"] = "params"
    return result


def _map_variant_payload(state: Mapping[str, Any], mode: str) -> dict[str, Any]:
    if mode == "single":
        return {
            "lead": int(state.get("lead", 24)),
            "basemap": str(state.get("basemap", "places")),
            "radius": int(state.get("radius", 100)),
        }
    return {
        "from": int(state.get("from", 0)),
        "to": int(state.get("to", 48)),
        "time_step": int(state.get("time_step", 3)),
        "basemap": str(state.get("basemap", "places")),
        "radius": int(state.get("radius", 100)),
    }


def _switch_map_mode(state: Mapping[str, Any], target_mode: str) -> dict[str, Any]:
    target_mode = target_mode if target_mode in {"gif", "single", "series"} else "gif"
    current_mode = str(state.get("mode", "gif"))
    variants_raw = state.get("_map_variants") or {}
    variants = {
        str(key): dict(value)
        for key, value in variants_raw.items()
        if isinstance(value, Mapping)
    } if isinstance(variants_raw, Mapping) else {}
    variants[current_mode] = _map_variant_payload(state, current_mode)

    defaults = default_product_params("map")["variants"]
    target = dict(variants.get(target_mode) or defaults[target_mode])
    result = dict(state)
    result["mode"] = target_mode
    for key in ("lead", "from", "to", "time_step", "basemap", "radius"):
        if key in target:
            result[key] = target[key]
    result["_map_variants"] = variants
    normalised = normalise_product_params(
        "map",
        {**result, "variants": variants},
    )
    return {
        **result,
        **_map_state_params(normalised),
        "product": "map",
        "mode": target_mode,
    }


def _normalise_map_state(state: Mapping[str, Any]) -> dict[str, Any]:
    normalised = normalise_product_params(
        "map",
        {**dict(state), "variants": state.get("_map_variants", {})},
    )
    result = dict(state)
    result.update(_map_state_params(normalised))
    return result


def _point_line(point: Mapping[str, Any] | None) -> str:
    if not point:
        return "📍 Точка не выбрана"
    return (
        f"📍 {point.get('label', 'точка')} · "
        f"{float(point['lat']):.4f}, {float(point['lon']):.4f}"
    )


def _summary(preference: ProductPreference) -> str:
    params = preference.params
    product = preference.product
    label = PRODUCT_LABELS.get(product, product)
    if product == "profile":
        return f"{label} · +{int(params.get('lead', 24))} ч"
    if product == "aero":
        return f"{label} · Skew-T · +{int(params.get('lead', 24))} ч"
    if product == "windgram":
        param = {"wind": "ветер", "temp": "температура", "rh": "влажность"}.get(
            str(params.get("param", "wind")),
            "ветер",
        )
        return f"{label} · {param} · до +{int(params.get('to', 120))} ч"
    if product == "cloudgram":
        mode = "кратко" if str(params.get("mode")) == "simple" else "подробно"
        return f"{label} · {mode} · до +{int(params.get('to', 72))} ч"
    if product == "map":
        mode = str(params.get("mode", "gif"))
        if mode == "single":
            return f"🗺 Одна карта · +{int(params.get('lead', 24))} ч"
        title = "🗺 Анимация" if mode == "gif" else "🗺 Серия PNG"
        return (
            f"{title} · +{int(params.get('from', 0))}…+{int(params.get('to', 48))} ч · "
            f"шаг {int(params.get('time_step', 3))} ч"
        )
    if product == "meteogram":
        return (
            f"{label} · {str(params.get('source_id', 'gfs')).upper()} · "
            f"{int(params.get('days', 5))} сут · "
            f"{str(params.get('output_format', 'png')).upper()}"
        )
    if product == "route":
        origin = params.get("origin") or {}
        destination = params.get("destination") or {}
        return (
            f"{label} · {origin.get('label', 'начало')} → "
            f"{destination.get('label', 'конец')} · +{int(params.get('lead', 24))} ч"
        )
    return label


def home_text(user_id: int) -> str:
    active = get_active_location(user_id)
    lines = ["🌦 GFS 0.25 · модельные прогнозы"]
    if active:
        lines.append(f"📍 {active.label} · {active.lat:.4f}, {active.lon:.4f}")
    else:
        lines.append("📍 Основная точка ещё не выбрана")
    quick = get_quick_preferences(user_id, 2)
    if quick:
        lines.extend(["", "Быстрые действия:"])
        lines.extend(_summary(item) for item in quick)
    lines.extend(["", "Выберите продукт.", "ℹ GFS grid, не наблюдение и не радиозонд."])
    return "\n".join(lines)


def home_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for preference in get_quick_preferences(user_id, 2):
        rows.append(
            [
                InlineKeyboardButton(
                    ("▶ " + _summary(preference))[:64],
                    callback_data=f"quick:{preference.product}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("📈 Профиль", callback_data="home:profile"),
                InlineKeyboardButton("🗺 Карта", callback_data="home:map"),
            ],
            [
                InlineKeyboardButton("☁️ Облака", callback_data="home:cloudgram"),
                InlineKeyboardButton("🟦 Срок × уровень", callback_data="home:windgram"),
            ],
            [
                InlineKeyboardButton("📊 Метеограмма", callback_data="home:meteogram"),
                InlineKeyboardButton("🧾 Аэродиаграмма", callback_data="home:aero"),
            ],
            [
                InlineKeyboardButton("✈️ Маршрут", callback_data="home:route"),
                InlineKeyboardButton("🕒 Расписания", callback_data="home:schedule"),
            ],
            [
                InlineKeyboardButton("⚙️ Настройки", callback_data="home:settings"),
                InlineKeyboardButton("❓ Помощь", callback_data="home:help"),
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _show_home(message, user_id: int, prefix: str | None = None) -> None:
    import telegram_concise_ux

    await telegram_concise_ux._remove_reply_keyboard(message)
    text = home_text(user_id)
    if prefix:
        text = f"{prefix}\n\n{text}"
    await message.reply_text(text, reply_markup=home_keyboard(user_id))


def _settings_text(user_id: int) -> str:
    active = get_active_location(user_id)
    lines = ["⚙️ Мои настройки", ""]
    lines.append(
        f"📍 Основная точка: {active.label}"
        if active
        else "📍 Основная точка: не выбрана"
    )
    preferences = [
        get_product_preference(user_id, product, include_selection=False)
        for product in ("map", "profile", "cloudgram", "windgram", "aero", "meteogram", "route")
    ]
    stored = [item for item in preferences if item]
    if stored:
        lines.extend(["", "Последние успешные параметры:"])
        lines.extend(_summary(item) for item in stored)
    else:
        lines.extend(["", "Успешных сохранённых расчётов пока нет."])
    lines.extend(
        [
            "",
            "Выбор продукта сохраняется сразу; быстрые действия появляются только после успешного расчёта.",
        ]
    )
    return "\n".join(lines)


def _settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("📍 Мои точки", callback_data="prefs:locations")]
    ]
    reset_buttons = []
    for product in ("map", "profile", "cloudgram", "windgram", "aero", "meteogram", "route"):
        if get_product_preference(user_id, product):
            reset_buttons.append(
                InlineKeyboardButton(
                    f"Сбросить {PRODUCT_LABELS[product].split(' ', 1)[-1]}",
                    callback_data=f"prefs:reset:{product}",
                )
            )
    for index in range(0, len(reset_buttons), 2):
        rows.append(reset_buttons[index : index + 2])
    rows.extend(
        [
            [InlineKeyboardButton("🧹 Очистить последние точки", callback_data="prefs:locations:confirm")],
            [InlineKeyboardButton("🗑 Удалить мои настройки", callback_data="prefs:all:confirm")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="prefs:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _locations_text(user_id: int) -> str:
    locations = get_recent_locations(user_id, 10)
    active = get_active_location(user_id)
    if not locations:
        return "📍 Мои точки\n\nСохранённых точек пока нет."
    lines = ["📍 Мои точки", "", "Выберите основную точку:"]
    for item in locations:
        marker = "✓" if active and item.location_id == active.location_id else "•"
        lines.append(f"{marker} {item.label} · {item.lat:.4f}, {item.lon:.4f}")
    return "\n".join(lines)


def _locations_keyboard(user_id: int) -> InlineKeyboardMarkup:
    active = get_active_location(user_id)
    rows = []
    for item in get_recent_locations(user_id, 10):
        marker = "✓ " if active and item.location_id == active.location_id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    (marker + item.label)[:58],
                    callback_data=f"prefs:location:{item.location_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("← Настройки", callback_data="home:settings")])
    return InlineKeyboardMarkup(rows)


def _profile_text(point: Mapping[str, Any], lead: int) -> str:
    return (
        "📈 Вертикальный профиль GFS\n"
        f"{_point_line(point)}\n"
        f"Срок: +{lead} ч\n\n"
        "Параметры сохранятся после выбора; быстрый повтор — после успешного расчёта."
    )


def _profile_keyboard(lead: int) -> InlineKeyboardMarkup:
    values = (0, 6, 12, 24, 48, 72)
    buttons = [
        InlineKeyboardButton(
            ("✓ " if value == lead else "") + f"+{value}ч",
            callback_data=f"personal:profile:lead:{value}",
        )
        for value in values
    ]
    return InlineKeyboardMarkup(
        [
            buttons[:3],
            buttons[3:],
            [InlineKeyboardButton(f"▶ Построить +{lead} ч", callback_data="personal:profile:run")],
            [
                InlineKeyboardButton("📍 Другая точка", callback_data="personal:profile:point"),
                InlineKeyboardButton("🏠 Меню", callback_data="prefs:home"),
            ],
        ]
    )


def _quick_card(preference: ProductPreference) -> tuple[str, InlineKeyboardMarkup]:
    point = preference.point
    text = _summary(preference)
    if point:
        text += "\n" + _point_line(point)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Построить", callback_data=f"quick:{preference.product}")],
            [
                InlineKeyboardButton("⚙️ Изменить", callback_data=f"personal:edit:{preference.product}"),
                InlineKeyboardButton("🏠 Меню", callback_data="prefs:home"),
            ],
        ]
    )
    return text, keyboard


def _params_from_request(
    product: str,
    request_text: str | None,
    lead_from: int | None,
    lead_to: int | None,
) -> dict[str, Any]:
    raw = str(request_text or "")
    values = {match.group("key").lower(): match.group("value") for match in _PARAM_RE.finditer(raw)}
    lead_match = _LEAD_RE.search(raw)
    canonical = "aero" if product == "skewt" else product
    if canonical == "profile":
        return normalise_product_params(
            "profile",
            {"lead": lead_from if lead_from is not None else int(lead_match.group("value")) if lead_match else 24},
        )
    if canonical == "aero":
        return normalise_product_params(
            "aero",
            {
                "lead": lead_from if lead_from is not None else int(lead_match.group("value")) if lead_match else 24,
                "diagram_type": values.get("type", "skewt"),
            },
        )
    if canonical == "windgram":
        return normalise_product_params(
            "windgram",
            {
                "from": values.get("from", lead_from or 0),
                "to": values.get("to", lead_to or 120),
                "time_step": values.get("step", 6),
                "top": values.get("top", 500),
                "param": values.get("param", "wind"),
            },
        )
    if canonical == "cloudgram":
        return normalise_product_params(
            "cloudgram",
            {
                "from": values.get("from", lead_from or 0),
                "to": values.get("to", lead_to or 72),
                "time_step": values.get("step", 3),
                "mode": values.get("mode", "pro"),
            },
        )
    if canonical == "map":
        mode = values.get("mode", "single" if lead_from == lead_to else "series")
        return normalise_product_params(
            "map",
            {
                "mode": mode,
                "lead": lead_to if lead_from == lead_to else 24,
                "from": values.get("from", lead_from or 0),
                "to": values.get("to", lead_to or 48),
                "time_step": values.get("step", 3),
                "basemap": values.get("basemap", "places"),
                "radius": values.get("radius", 100),
            },
        )
    return default_product_params(canonical)


def _map_command_options(params: Mapping[str, Any], raw: str) -> str:
    value = normalise_product_params("map", params)
    mode = str(value.get("mode", "gif"))
    if mode == "single":
        options = f"+{int(value.get('lead', 24))}"
    else:
        options = (
            f"from={int(value.get('from', 0))} "
            f"to={int(value.get('to', 48))} "
            f"step={int(value.get('time_step', 3))} mode={mode}"
        )
    if not re.search(r"(?:^|\s)radius=", raw, re.IGNORECASE):
        radius = int(value.get("radius", 100))
        if radius != 100:
            options += f" radius={radius}"
    if not re.search(r"(?:^|\s)(?:basemap|base|подложка)=", raw, re.IGNORECASE):
        basemap = str(value.get("basemap", "places"))
        if basemap != "places":
            options += f" basemap={basemap}"
    return options


def _patch_external_modules() -> None:
    import telegram_meteogram
    import telegram_route

    if not getattr(telegram_meteogram, "_PERSISTENT_PREFERENCES_PATCHED", False):
        original_run = telegram_meteogram._run_product

        async def run_meteogram(message, point, request, user=None, *, output_format="png"):
            result = await original_run(
                message,
                point,
                request,
                user,
                output_format=output_format,
            )
            if result is not False and not _is_scheduled_message(message):
                user_id = int(
                    getattr(user, "id", 0)
                    or getattr(getattr(message, "from_user", None), "id", 0)
                    or 0
                )
                if user_id > 0:
                    record_product_success(
                        user_id,
                        "meteogram",
                        {
                            "source_id": request.source_id,
                            "days": request.days,
                            "output_format": output_format,
                        },
                        point,
                    )
            return result

        original_callback = telegram_meteogram.meteogram_callback

        async def meteogram_callback(update, context):
            try:
                await original_callback(update, context)
            finally:
                state = context.user_data.get(telegram_meteogram.SESSION_KEY)
                if (
                    isinstance(state, dict)
                    and isinstance(state.get("point"), dict)
                    and not state.get("_schedule_setup")
                ):
                    params = {
                        "source_id": state.get("source_id", "gfs"),
                        "days": state.get("days", 5),
                        "output_format": state.get("output_format", "png"),
                    }
                    save_product_selection(
                        _user_id_from_update(update),
                        "meteogram",
                        params,
                        state["point"],
                    )

        telegram_meteogram._run_product = run_meteogram
        telegram_meteogram.meteogram_callback = meteogram_callback
        telegram_meteogram._PERSISTENT_PREFERENCES_PATCHED = True

    if not getattr(telegram_route, "_PERSISTENT_PREFERENCES_PATCHED", False):
        original_run = telegram_route.run_route_product

        async def run_route(message, origin, destination, parsed, user=None):
            result = await original_run(message, origin, destination, parsed, user=user)
            if result is not False and not _is_scheduled_message(message):
                user_id = int(
                    getattr(user, "id", 0)
                    or getattr(getattr(message, "from_user", None), "id", 0)
                    or 0
                )
                if user_id > 0:
                    record_product_success(
                        user_id,
                        "route",
                        {
                            "origin": _pack_point(origin),
                            "destination": _pack_point(destination),
                            "lead": parsed.departure_lead,
                            "speed": parsed.speed_kmh,
                            "mode": parsed.mode,
                            "spatial_step": parsed.spatial_step_km,
                        },
                        origin,
                    )
            return result

        original_callback = telegram_route.route_callback

        async def route_callback(update, context):
            try:
                await original_callback(update, context)
            finally:
                state = context.user_data.get(telegram_route.ROUTE_SESSION_KEY)
                if isinstance(state, dict) and state.get("step") == "settings":
                    origin = state.get("origin")
                    destination = state.get("destination")
                    if isinstance(origin, dict) and isinstance(destination, dict):
                        save_product_selection(
                            _user_id_from_update(update),
                            "route",
                            {
                                "origin": origin,
                                "destination": destination,
                                "lead": state.get("lead", 24),
                                "speed": state.get("speed", 300),
                                "mode": state.get("mode", "simple"),
                                "spatial_step": state.get("spatial_step", 50),
                            },
                            origin,
                        )

        telegram_route.run_route_product = run_route
        telegram_route.route_callback = route_callback
        # Route endpoints are useful history, but must not replace the active point.
        telegram_route.remember_location = remember_location_without_activation
        telegram_route._PERSISTENT_PREFERENCES_PATCHED = True


def _patch_schedule_result_keyboard() -> None:
    try:
        import telegram_schedule_ux
    except Exception:
        return
    if getattr(telegram_schedule_ux, "_PERSONAL_RESULT_KEYBOARD_PATCHED", False):
        return

    def quick_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🕒 В расписание", callback_data="sched:quick")],
                [
                    InlineKeyboardButton("🔄 Обновить", callback_data="quick:last"),
                    InlineKeyboardButton("⚙️ Изменить", callback_data="personal:last:change"),
                ],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="prefs:home")],
            ]
        )

    telegram_schedule_ux._quick_keyboard = quick_keyboard
    telegram_schedule_ux._PERSONAL_RESULT_KEYBOARD_PATCHED = True


def install(namespace: dict[str, Any]) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    _patch_external_modules()
    _patch_schedule_result_keyboard()

    import telegram_product_wizard as wizard

    wizard.MAP_MODES = (
        ("gif", "Анимация"),
        ("single", "Одна карта"),
        ("series", "Серия PNG"),
    )
    wizard.CLOUDGRAM_MODES = (("pro", "Подробно"), ("simple", "Кратко"))

    def map_to_options(state):
        lead_from = int(state.get("from", 0))
        return tuple(value for value in (24, 48, 72, 96) if value >= lead_from)

    wizard._map_to_options = map_to_options

    original_start_product_wizard = namespace["_start_product_wizard"]
    original_show_wizard_params = namespace["_show_wizard_params"]
    original_run_wizard_product = namespace["_run_wizard_product"]
    original_tracked_product = namespace["_tracked_product"]
    original_product_callback = namespace["product_wizard_callback"]
    original_profile_command = namespace["profile_command"]
    original_map_command = namespace["map_command"]

    async def start_product_wizard(message, context, state):
        user_id = int(context.user_data.get("_ux_home_user_id", 0) or 0) or _user_id_from_message(message)
        if user_id <= 0 or state.get("_schedule_setup"):
            await original_start_product_wizard(message, context, state)
            return
        personalised = _state_for_user(user_id, state)
        if not isinstance(personalised.get("point"), dict):
            await original_start_product_wizard(message, context, personalised)
            return
        context.user_data.pop("_ux_home_user_id", None)
        namespace["_clear_pending"](context)
        context.user_data[PRODUCT_WIZARD_KEY] = personalised
        import telegram_concise_ux

        await telegram_concise_ux._remove_reply_keyboard(message)
        await message.reply_text(
            namespace["params_text"](personalised),
            parse_mode=ParseMode.HTML,
            reply_markup=namespace["params_keyboard"](personalised),
        )

    async def show_wizard_params(message, context, state):
        await original_show_wizard_params(message, context, state)
        if not state.get("_schedule_setup") and isinstance(state.get("point"), dict):
            save_product_selection(
                _user_id_from_message(message),
                str(state.get("product", "")),
                _state_params(state),
                state["point"],
            )

    async def run_wizard_product(message, context, state, user=None):
        state_value = _normalise_map_state(state) if str(state.get("product")) == "map" else dict(state)
        token = _PENDING_WIZARD_STATE.set(state_value)
        try:
            await original_run_wizard_product(message, context, state_value, user=user)
        finally:
            _PENDING_WIZARD_STATE.reset(token)

    async def tracked_product(
        message,
        product,
        city,
        request_text,
        runner,
        *,
        lead_from=None,
        lead_to=None,
        run=None,
        user=None,
    ):
        result = await original_tracked_product(
            message,
            product,
            city,
            request_text,
            runner,
            lead_from=lead_from,
            lead_to=lead_to,
            run=run,
            user=user,
        )
        if result is False or _is_scheduled_message(message):
            return result
        actual_user = user or getattr(message, "from_user", None)
        user_id = int(getattr(actual_user, "id", 0) or 0)
        if user_id <= 0:
            return result
        canonical = "aero" if str(product) == "skewt" else str(product)
        pending = _PENDING_WIZARD_STATE.get()
        if isinstance(pending, dict) and str(pending.get("product")) == canonical:
            params = _state_params(pending)
            point = pending.get("point")
        else:
            params = _params_from_request(canonical, request_text, lead_from, lead_to)
            active = get_active_location(user_id)
            point = _pack_point(active)
        if point and canonical in PRODUCT_LABELS:
            record_product_success(user_id, canonical, params, point)
        return result

    async def product_wizard_callback(update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        if query and data == "wiz:cancel":
            await query.answer()
            context.user_data.pop(PRODUCT_WIZARD_KEY, None)
            user_id = _user_id_from_update(update)
            await query.edit_message_text(home_text(user_id), reply_markup=home_keyboard(user_id))
            raise ApplicationHandlerStop

        state = context.user_data.get(PRODUCT_WIZARD_KEY)
        if isinstance(state, dict) and str(state.get("product")) == "map":
            if data.startswith("wiz:map:mode:"):
                target = data.rsplit(":", 1)[1]
                context.user_data[PRODUCT_WIZARD_KEY] = _switch_map_mode(state, target)
            elif data.startswith("wiz:map:to:"):
                candidate = dict(state)
                candidate["to"] = int(data.rsplit(":", 1)[1])
                candidate["from"] = min(int(candidate.get("from", 0)), int(candidate["to"]))
                context.user_data[PRODUCT_WIZARD_KEY] = _normalise_map_state(candidate)

        if data == "wiz:run" and isinstance(state, dict) and isinstance(state.get("point"), dict):
            save_product_selection(
                _user_id_from_update(update),
                str(state.get("product", "")),
                _state_params(state),
                state["point"],
            )
        await original_product_callback(update, context)
        state = context.user_data.get(PRODUCT_WIZARD_KEY)
        if (
            isinstance(state, dict)
            and isinstance(state.get("point"), dict)
            and not state.get("_schedule_setup")
            and data != "wiz:run"
        ):
            if str(state.get("product")) == "map":
                state = _normalise_map_state(state)
                context.user_data[PRODUCT_WIZARD_KEY] = state
            save_product_selection(
                _user_id_from_update(update),
                str(state.get("product", "")),
                _state_params(state),
                state["point"],
            )

    async def profile_command(update, context):
        raw = " ".join(context.args or []).strip()
        if raw:
            await original_profile_command(update, context)
            return
        message = update.effective_message
        user_id = _user_id_from_update(update)
        preference = get_product_preference(user_id, "profile")
        point = _preference_point(user_id, preference)
        if not message or not point:
            await original_profile_command(update, context)
            return
        lead = int((preference.params if preference else default_product_params("profile")).get("lead", 24))
        context.user_data["personal_profile"] = {"point": point, "lead": lead}
        import telegram_concise_ux

        await telegram_concise_ux._remove_reply_keyboard(message)
        await message.reply_text(_profile_text(point, lead), reply_markup=_profile_keyboard(lead))

    async def map_command(update, context):
        raw = " ".join(context.args or []).strip()
        if not raw or _MAP_TIME_RE.search(raw):
            await original_map_command(update, context)
            return
        user_id = _user_id_from_update(update)
        preference = get_product_preference(user_id, "map")
        params = preference.params if preference else default_product_params("map")
        options = _map_command_options(params, raw)
        old_args = context.args
        context.args = [*(context.args or []), *options.split()]
        try:
            await original_map_command(update, context)
        finally:
            context.args = old_args

    async def start(update, context):
        message = update.effective_message
        if not message:
            return
        namespace["_clear_pending"](context)
        context.user_data.pop("route_profile_wizard", None)
        await _show_home(message, _user_id_from_update(update))

    async def cancel_command(update, context):
        namespace["_clear_pending"](context)
        context.user_data.pop("route_profile_wizard", None)
        context.user_data.pop("personal_profile", None)
        message = update.effective_message
        if message:
            await _show_home(message, _user_id_from_update(update), "Текущий выбор сброшен.")

    async def help_command(update, context):
        message = update.effective_message
        if not message:
            return
        import telegram_concise_ux

        await telegram_concise_ux._remove_reply_keyboard(message)
        text = telegram_concise_ux.help_text().replace(
            "/map Москва from=0 to=24 step=3 mode=gif",
            "/map Москва from=0 to=48 step=3 mode=gif",
        )
        text += (
            "\n\n/settings — основная точка и сохранённые параметры. "
            "Карта без прежних настроек: анимация +0…+48 ч, шаг 3 ч."
        )
        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=home_keyboard(_user_id_from_update(update)),
        )

    namespace.update(
        {
            "_start_product_wizard": start_product_wizard,
            "_show_wizard_params": show_wizard_params,
            "_run_wizard_product": run_wizard_product,
            "_tracked_product": tracked_product,
            "product_wizard_callback": product_wizard_callback,
            "profile_command": profile_command,
            "map_command": map_command,
            "start": start,
            "cancel_command": cancel_command,
            "help_command": help_command,
        }
    )


def _default_preference(user_id: int, product: str) -> ProductPreference | None:
    point = _preference_point(user_id, get_product_preference(user_id, product))
    if not point:
        return None
    preference = get_product_preference(user_id, product)
    if preference:
        return preference
    return ProductPreference(
        user_id=user_id,
        product=product,
        params=default_product_params(product),
        point=point,
        success_count=0,
        selected_at=None,
        last_success_at=None,
        kind="default",
    )


async def _run_preference(update, context, namespace: dict[str, Any], preference: ProductPreference) -> None:
    message = update.effective_message
    if not message:
        return
    point_payload = _preference_point(preference.user_id, preference)
    if preference.product != "route" and not point_payload:
        await message.reply_text("Сохранённая точка отсутствует. Выберите точку заново.")
        return

    product = preference.product
    if product == "profile":
        point = _unpack_point(point_payload)
        await namespace["_tracked_run_profile"](
            message,
            point,
            int(preference.params.get("lead", 24)),
            None,
            user=update.effective_user,
        )
        return
    if product in {"aero", "windgram", "cloudgram", "map"}:
        params = _map_state_params(preference.params) if product == "map" else dict(preference.params)
        state = {"product": product, "step": "params", "point": point_payload, **params}
        await namespace["_run_wizard_product"](
            message,
            context,
            state,
            user=update.effective_user,
        )
        return
    if product == "meteogram":
        import telegram_meteogram
        from meteogram_request import MeteogramRequest

        point = _unpack_point(point_payload)
        request = MeteogramRequest(
            f"{point.lat} {point.lon}",
            str(preference.params.get("source_id", "gfs")),
            int(preference.params.get("days", 5)),
        )
        await telegram_meteogram._run_product(
            message,
            point,
            request,
            update.effective_user,
            output_format=str(preference.params.get("output_format", "png")),
        )
        return
    if product == "route":
        import telegram_route

        origin_payload = preference.params.get("origin")
        destination_payload = preference.params.get("destination")
        if not isinstance(origin_payload, Mapping) or not isinstance(destination_payload, Mapping):
            await message.reply_text("Сохранённый маршрут повреждён. Создайте маршрут заново.")
            return
        origin = _unpack_point(origin_payload)
        destination = _unpack_point(destination_payload)
        parsed = telegram_route.ParsedRouteRequest(
            origin_query=origin.label,
            destination_query=destination.label,
            departure_lead=int(preference.params.get("lead", 24)),
            speed_kmh=int(preference.params.get("speed", 300)),
            mode=str(preference.params.get("mode", "simple")),
            run=None,
            spatial_step_km=int(preference.params.get("spatial_step", 50)),
            step_explicit=True,
        )
        await telegram_route.run_route_product(
            message,
            origin,
            destination,
            parsed,
            user=update.effective_user,
        )


async def _edit_preference_card(update, context, namespace: dict[str, Any], preference: ProductPreference) -> None:
    query = update.callback_query
    if not query:
        return
    point_payload = _preference_point(preference.user_id, preference)
    product = preference.product
    namespace["_clear_pending"](context)
    if product == "profile" and point_payload:
        lead = int(preference.params.get("lead", 24))
        context.user_data["personal_profile"] = {"point": point_payload, "lead": lead}
        await query.edit_message_text(_profile_text(point_payload, lead), reply_markup=_profile_keyboard(lead))
        return
    if product in {"aero", "windgram", "cloudgram", "map"} and point_payload:
        params = _map_state_params(preference.params) if product == "map" else dict(preference.params)
        state = {"product": product, "step": "params", "point": point_payload, **params}
        context.user_data[PRODUCT_WIZARD_KEY] = state
        await query.edit_message_text(
            namespace["params_text"](state),
            parse_mode=ParseMode.HTML,
            reply_markup=namespace["params_keyboard"](state),
        )
        return
    if product == "meteogram" and point_payload:
        context.user_data["personal_meteogram"] = {
            "point": point_payload,
            "params": dict(preference.params),
        }
        text, keyboard = _quick_card(preference)
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    if product == "route":
        text, keyboard = _quick_card(preference)
        await query.edit_message_text(text, reply_markup=keyboard)
        return
    await query.edit_message_text("Сохранённый вариант недоступен.", reply_markup=home_keyboard(preference.user_id))


def register(application, namespace: dict[str, Any]) -> None:
    async def settings_command(update, context):
        message = update.effective_message
        if message:
            user_id = _user_id_from_update(update)
            await message.reply_text(
                _settings_text(user_id),
                reply_markup=_settings_keyboard(user_id),
            )
        raise ApplicationHandlerStop

    async def optional_meteogram_command(update, context):
        if context.args:
            return
        user_id = _user_id_from_update(update)
        preference = _default_preference(user_id, "meteogram")
        message = update.effective_message
        if preference is None or not message:
            return
        context.user_data["personal_meteogram"] = {
            "point": preference.point,
            "params": dict(preference.params),
        }
        text, keyboard = _quick_card(preference)
        await message.reply_text(text, reply_markup=keyboard)
        raise ApplicationHandlerStop

    async def optional_route_command(update, context):
        if context.args:
            return
        preference = get_product_preference(
            _user_id_from_update(update),
            "route",
            include_selection=False,
        )
        message = update.effective_message
        if preference is None or not message:
            return
        text, keyboard = _quick_card(preference)
        await message.reply_text(text, reply_markup=keyboard)
        raise ApplicationHandlerStop

    async def personal_callback(update, context):
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        user_id = _user_id_from_update(update)

        if data in {"home:settings", "prefs:settings"}:
            await query.answer()
            await query.edit_message_text(
                _settings_text(user_id),
                reply_markup=_settings_keyboard(user_id),
            )
            raise ApplicationHandlerStop
        if data == "prefs:home":
            await query.answer()
            await query.edit_message_text(home_text(user_id), reply_markup=home_keyboard(user_id))
            raise ApplicationHandlerStop
        if data == "prefs:locations":
            await query.answer()
            await query.edit_message_text(
                _locations_text(user_id),
                reply_markup=_locations_keyboard(user_id),
            )
            raise ApplicationHandlerStop
        if data.startswith("prefs:location:"):
            location_id = int(data.rsplit(":", 1)[1])
            changed = set_active_location(user_id, location_id)
            await query.answer("Основная точка изменена" if changed else "Точка недоступна")
            await query.edit_message_text(
                _locations_text(user_id),
                reply_markup=_locations_keyboard(user_id),
            )
            raise ApplicationHandlerStop
        if data.startswith("prefs:reset:"):
            product = data.rsplit(":", 1)[1]
            clear_product_preference(user_id, product)
            await query.answer("Настройки продукта сброшены")
            await query.edit_message_text(
                _settings_text(user_id),
                reply_markup=_settings_keyboard(user_id),
            )
            raise ApplicationHandlerStop
        if data == "prefs:locations:confirm":
            await query.answer()
            await query.edit_message_text(
                "Удалить историю и основную точку? Настройки продуктов и расписания останутся.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Удалить точки", callback_data="prefs:locations:delete")],
                        [InlineKeyboardButton("Отмена", callback_data="home:settings")],
                    ]
                ),
            )
            raise ApplicationHandlerStop
        if data == "prefs:locations:delete":
            clear_locations(user_id)
            await query.answer("Точки удалены")
            await query.edit_message_text(
                _settings_text(user_id),
                reply_markup=_settings_keyboard(user_id),
            )
            raise ApplicationHandlerStop
        if data == "prefs:all:confirm":
            await query.answer()
            await query.edit_message_text(
                "Удалить все сохранённые точки и пользовательские параметры? Расписания удаляются отдельно в /schedule.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Удалить мои настройки", callback_data="prefs:all:delete")],
                        [InlineKeyboardButton("Отмена", callback_data="home:settings")],
                    ]
                ),
            )
            raise ApplicationHandlerStop
        if data == "prefs:all:delete":
            clear_user_data(user_id)
            context.user_data.clear()
            await query.answer("Настройки удалены")
            await query.edit_message_text(home_text(user_id), reply_markup=home_keyboard(user_id))
            raise ApplicationHandlerStop

        if data in {"home:meteogram", "home:route"}:
            product = data.split(":", 1)[1]
            preference = (
                _default_preference(user_id, product)
                if product == "meteogram"
                else get_product_preference(user_id, product, include_selection=False)
            )
            if preference is None:
                return
            await query.answer()
            if product == "meteogram":
                context.user_data["personal_meteogram"] = {
                    "point": preference.point,
                    "params": dict(preference.params),
                }
            text, keyboard = _quick_card(preference)
            await query.edit_message_text(text, reply_markup=keyboard)
            raise ApplicationHandlerStop

        if data.startswith("quick:"):
            await query.answer()
            product = data.split(":", 1)[1]
            preference = (
                get_last_success_preference(user_id)
                if product == "last"
                else get_product_preference(user_id, product, include_selection=False)
            )
            if preference is None:
                await query.edit_message_text("Успешный сохранённый вариант не найден.", reply_markup=home_keyboard(user_id))
                raise ApplicationHandlerStop
            await query.edit_message_text(f"Запускаю: {html.escape(_summary(preference))}…")
            await _run_preference(update, context, namespace, preference)
            raise ApplicationHandlerStop

        if data == "personal:last:change":
            await query.answer()
            preference = get_last_success_preference(user_id)
            if preference is None:
                await query.edit_message_text("Успешный сохранённый вариант не найден.", reply_markup=home_keyboard(user_id))
            else:
                await _edit_preference_card(update, context, namespace, preference)
            raise ApplicationHandlerStop
        if data.startswith("personal:edit:"):
            await query.answer()
            product = data.rsplit(":", 1)[1]
            preference = get_product_preference(user_id, product)
            if preference is None:
                preference = _default_preference(user_id, product)
            if preference is None:
                await query.edit_message_text("Сначала выберите точку.", reply_markup=home_keyboard(user_id))
            else:
                await _edit_preference_card(update, context, namespace, preference)
            raise ApplicationHandlerStop

        if data.startswith("personal:profile:lead:"):
            await query.answer()
            state = context.user_data.get("personal_profile")
            if not isinstance(state, dict) or not isinstance(state.get("point"), dict):
                await query.edit_message_text("Выбор устарел. Откройте профиль заново.", reply_markup=home_keyboard(user_id))
                raise ApplicationHandlerStop
            state["lead"] = int(data.rsplit(":", 1)[1])
            save_product_selection(user_id, "profile", {"lead": state["lead"]}, state["point"])
            await query.edit_message_text(
                _profile_text(state["point"], int(state["lead"])),
                reply_markup=_profile_keyboard(int(state["lead"])),
            )
            raise ApplicationHandlerStop
        if data == "personal:profile:run":
            await query.answer()
            state = context.user_data.pop("personal_profile", None)
            if not isinstance(state, dict) or not isinstance(state.get("point"), dict):
                await query.edit_message_text("Выбор устарел. Откройте профиль заново.", reply_markup=home_keyboard(user_id))
                raise ApplicationHandlerStop
            point = _unpack_point(state["point"])
            lead = int(state.get("lead", 24))
            await query.edit_message_text(f"Строю профиль +{lead} ч…")
            await namespace["_tracked_run_profile"](
                query.message,
                point,
                lead,
                None,
                user=update.effective_user,
            )
            raise ApplicationHandlerStop
        if data == "personal:profile:point":
            await query.answer()
            context.user_data.pop("personal_profile", None)
            namespace["_clear_pending"](context)
            await query.edit_message_text("Выберите другую точку для профиля.")
            if query.message:
                await query.message.reply_text(
                    "📈 Вертикальный профиль\n\nУкажите город, координаты или отправьте геолокацию.",
                    reply_markup=namespace["_location_keyboard_for_user"](user_id),
                )
            raise ApplicationHandlerStop

        if data == "personal:meteogram:run":
            await query.answer()
            state = context.user_data.get("personal_meteogram")
            if not isinstance(state, dict):
                await query.edit_message_text("Выбор устарел.", reply_markup=home_keyboard(user_id))
                raise ApplicationHandlerStop
            preference = ProductPreference(
                user_id=user_id,
                product="meteogram",
                params=dict(state["params"]),
                point=dict(state["point"]),
                success_count=0,
                selected_at=None,
                last_success_at=None,
                kind="selected",
            )
            await query.edit_message_text(f"Запускаю: {_summary(preference)}…")
            await _run_preference(update, context, namespace, preference)
            raise ApplicationHandlerStop
        if data == "personal:meteogram:change":
            await query.answer()
            import telegram_meteogram

            state = context.user_data.get("personal_meteogram")
            if not isinstance(state, dict):
                await query.edit_message_text("Выбор устарел.", reply_markup=home_keyboard(user_id))
                raise ApplicationHandlerStop
            context.user_data[telegram_meteogram.SESSION_KEY] = {
                "step": "type",
                "point": dict(state["point"]),
            }
            await query.edit_message_text("Выберите тип прогноза.", reply_markup=telegram_meteogram._type_keyboard())
            raise ApplicationHandlerStop

        if data == "personal:route:run":
            await query.answer()
            preference = get_product_preference(user_id, "route", include_selection=False)
            if preference is None:
                await query.edit_message_text("Сохранённый маршрут не найден.", reply_markup=home_keyboard(user_id))
                raise ApplicationHandlerStop
            await query.edit_message_text(f"Запускаю: {_summary(preference)}…")
            await _run_preference(update, context, namespace, preference)
            raise ApplicationHandlerStop
        if data == "personal:route:change":
            await query.answer()
            import telegram_route

            preference = get_product_preference(user_id, "route")
            params = preference.params if preference else default_product_params("route")
            context.user_data[telegram_route.ROUTE_SESSION_KEY] = {
                "step": "await_route",
                "lead": int(params.get("lead", 24)),
                "speed": int(params.get("speed", 300)),
                "mode": str(params.get("mode", "simple")),
                "spatial_step": int(params.get("spatial_step", 50)),
            }
            await query.edit_message_text("Введите новый маршрут через → или ->. Пример: Москва -> Санкт-Петербург")
            raise ApplicationHandlerStop

    application.add_handler(CommandHandler("settings", settings_command), group=-6)
    application.add_handler(CommandHandler("meteogram", optional_meteogram_command), group=-6)
    application.add_handler(CommandHandler("route", optional_route_command), group=-6)
    application.add_handler(
        CallbackQueryHandler(
            personal_callback,
            pattern=(
                r"^(?:quick:|prefs:|personal:|home:(?:settings|meteogram|route)$)"
            ),
        ),
        group=-6,
    )
