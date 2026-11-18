from __future__ import annotations

"""Add route schedules to the established Telegram scheduler.

The large Telegram scheduler remains stable.  This adapter only fills the last
product-parity gap and delegates actual execution to ``telegram_route`` whose
runner is already backed by the common messenger route service.
"""

from types import SimpleNamespace
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

SCHEDULE_ROUTE_SETUP_KEY = "schedule_route_setup"
_INSTALLED = False


def _route_spec(state: dict[str, object]) -> dict[str, object]:
    origin = state.get("origin")
    destination = state.get("destination")
    if not isinstance(origin, dict) or not isinstance(destination, dict):
        raise ValueError("Сначала задайте начало и конец маршрута")
    return {
        "product": "route",
        # The departure point defines the local timezone of this schedule.
        "point": dict(origin),
        "params": {
            "origin": dict(origin),
            "destination": dict(destination),
            "lead": int(state.get("lead", 24)),
            "speed": int(state.get("speed", 300)),
            "mode": str(state.get("mode", "simple")),
            "spatial_step": int(state.get("spatial_step", 50)),
        },
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    import telegram_route
    import telegram_schedules as schedules

    original_product_keyboard = schedules._product_keyboard
    original_product_title = schedules._product_title
    original_params_summary = schedules._params_summary
    original_start_product_setup = schedules._start_product_setup
    original_execute_schedule = schedules.execute_schedule

    def product_keyboard() -> InlineKeyboardMarkup:
        # Keep the established product order and add route as the seventh item.
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📈 Профиль", callback_data="sched:product:profile"),
                    InlineKeyboardButton("🧾 Аэродиаграмма", callback_data="sched:product:aero"),
                ],
                [
                    InlineKeyboardButton("🟦 Срок × уровень", callback_data="sched:product:windgram"),
                    InlineKeyboardButton("☁️ Облака", callback_data="sched:product:cloudgram"),
                ],
                [
                    InlineKeyboardButton("🗺️ Карта / анимация", callback_data="sched:product:map"),
                    InlineKeyboardButton("📊 Метеограмма / отчёт", callback_data="sched:product:meteogram"),
                ],
                [InlineKeyboardButton("✈ Маршрут", callback_data="sched:product:route")],
                [InlineKeyboardButton("← Расписания", callback_data="sched:list")],
            ]
        )

    def product_title(product: str) -> str:
        return "✈ Маршрут" if str(product) == "route" else original_product_title(product)

    def params_summary(item_or_spec) -> str:
        if isinstance(item_or_spec, schedules.ProductSchedule):
            product = item_or_spec.product
            params = item_or_spec.params
        else:
            product = str(item_or_spec["product"])
            params = dict(item_or_spec.get("params") or {})
        if product != "route":
            return original_params_summary(item_or_spec)
        origin = params.get("origin") if isinstance(params.get("origin"), dict) else {}
        destination = params.get("destination") if isinstance(params.get("destination"), dict) else {}
        return (
            f"{origin.get('label', 'старт')} → {destination.get('label', 'финиш')} · "
            f"вылет +{int(params.get('lead', 24))} ч · {int(params.get('speed', 300))} км/ч · "
            f"сетка {int(params.get('spatial_step', 50))} км · {str(params.get('mode', 'simple'))}"
        )

    async def start_product_setup(update, context, namespace: dict[str, Any], product: str) -> None:
        if product != "route":
            await original_start_product_setup(update, context, namespace, product)
            return
        query = update.callback_query
        if not query or not query.message:
            return
        user_id = schedules._user_id(update)
        if len(schedules.schedule_store().list_for_user(user_id)) >= schedules.MAX_SCHEDULES_PER_USER:
            await schedules._show_manager(query, user_id)
            return
        namespace["_clear_pending"](context)
        context.user_data[SCHEDULE_ROUTE_SETUP_KEY] = True
        context.user_data[telegram_route.ROUTE_SESSION_KEY] = {
            "step": "await_route",
            "lead": 24,
            "speed": int(telegram_route.ROUTE_DEFAULT_SPEED_KMH),
            "mode": "simple",
            "spatial_step": int(telegram_route.ROUTE_SPATIAL_STEP_KM),
        }
        await query.edit_message_text("Настраиваем маршрут для расписания.")
        await query.message.reply_text(
            "✈ Маршрутный профиль GFS\n\n"
            "Введите начало и конец через → или ->.\n"
            "Пример: Москва -> Санкт-Петербург\n\n"
            "После этого выберите срок, скорость, режим и сетку."
        )

    async def execute_schedule(application, namespace: dict[str, Any], item) -> bool:
        if item.product != "route":
            return bool(await original_execute_schedule(application, namespace, item))
        params = item.params
        origin_raw = params.get("origin")
        destination_raw = params.get("destination")
        if not isinstance(origin_raw, dict) or not isinstance(destination_raw, dict):
            raise schedules.ScheduleError("В route-расписании отсутствуют origin/destination")
        origin = telegram_route._unpack_point(origin_raw)
        destination = telegram_route._unpack_point(destination_raw)
        parsed = telegram_route.ParsedRouteRequest(
            origin_query=origin.label,
            destination_query=destination.label,
            departure_lead=int(params.get("lead", 24)),
            speed_kmh=int(params.get("speed", 300)),
            mode=str(params.get("mode", "simple")),
            run=None,
            spatial_step_km=int(params.get("spatial_step", 50)),
            step_explicit=True,
        )
        message = schedules.ScheduledMessage(application.bot, item)
        user = SimpleNamespace(id=item.user_id, username=item.username)
        async with schedules.SCHEDULE_SEMAPHORE:
            return bool(await telegram_route.run_route_product(message, origin, destination, parsed, user=user))

    schedules._product_keyboard = product_keyboard
    schedules._product_title = product_title
    schedules._params_summary = params_summary
    schedules._start_product_setup = start_product_setup
    schedules.execute_schedule = execute_schedule


def register(application) -> None:
    """Intercept route completion before the normal Telegram route handler."""

    import telegram_route
    import telegram_schedules as schedules

    async def route_schedule_callback(update, context) -> None:
        if not context.user_data.get(SCHEDULE_ROUTE_SETUP_KEY):
            return
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        if data == "route:cancel":
            context.user_data.pop(SCHEDULE_ROUTE_SETUP_KEY, None)
            return  # let the normal route handler render its native cancel UX
        if data != "route:run":
            return
        state = context.user_data.get(telegram_route.ROUTE_SESSION_KEY)
        if not isinstance(state, dict) or state.get("step") != "settings":
            return
        await query.answer()
        try:
            spec = _route_spec(state)
        except (ValueError, TypeError, KeyError) as exc:
            await query.edit_message_text(f"Ошибка расписания маршрута: {exc}")
            raise ApplicationHandlerStop
        context.user_data.pop(SCHEDULE_ROUTE_SETUP_KEY, None)
        context.user_data.pop(telegram_route.ROUTE_SESSION_KEY, None)
        await schedules._begin_timing(query, context, spec)
        raise ApplicationHandlerStop

    application.add_handler(
        CallbackQueryHandler(route_schedule_callback, pattern=r"^route:(?:run|cancel)$"),
        group=-10,
    )
