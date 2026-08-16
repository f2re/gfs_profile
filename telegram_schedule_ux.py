from __future__ import annotations

"""UX safety layer for Telegram schedule setup.

The schedule manager reuses the existing product wizards. This module keeps the
selected product as an independent guard state so a lost nested wizard can never
fall through to the generic profile text handler. It also adds a compact
"add this result to schedule" action for successful interactive products.
"""

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, MessageHandler, filters

import telegram_schedules as schedules

PRODUCT_SETUP_KEY = "schedule_product_setup"
QUICK_SPEC_KEY = "schedule_quick_spec"

_LAST_RESULT: dict[tuple[int, str], bool] = {}
_LAST_SPEC: dict[tuple[int, str], dict[str, object]] = {}
_INSTALLED = False


def _user_id(value: Any) -> int:
    user = getattr(value, "effective_user", None) or value
    return int(getattr(user, "id", 0) or 0)


def _message_user_id(message: Any, user: Any = None) -> int:
    return int(
        getattr(user, "id", 0)
        or getattr(getattr(message, "from_user", None), "id", 0)
        or 0
    )


def _is_scheduled_message(message: Any) -> bool:
    scheduled_type = getattr(schedules, "ScheduledMessage", None)
    return bool(scheduled_type is not None and isinstance(message, scheduled_type))


def _clear_product_setup(context) -> None:
    context.user_data.pop(PRODUCT_SETUP_KEY, None)
    context.user_data.pop(schedules.SCHEDULE_PROFILE_SETUP_KEY, None)


def _clear_nested_product_state(context) -> None:
    _clear_product_setup(context)
    context.user_data.pop(getattr(schedules, "PRODUCT_WIZARD_KEY", "product_wizard"), None)
    try:
        import telegram_meteogram

        context.user_data.pop(telegram_meteogram.SESSION_KEY, None)
    except Exception:
        pass


def _mark_product_setup(context, product: str) -> None:
    context.user_data[PRODUCT_SETUP_KEY] = {"product": str(product)}


def _setup_product(context) -> str | None:
    value = context.user_data.get(PRODUCT_SETUP_KEY)
    if not isinstance(value, dict):
        return None
    product = str(value.get("product") or "").strip()
    return product or None


def _ensure_meteogram_schedule_state(context) -> dict[str, object]:
    import telegram_meteogram

    state = context.user_data.get(telegram_meteogram.SESSION_KEY)
    if not isinstance(state, dict):
        state = {"step": "point", "_schedule_setup": True}
        context.user_data[telegram_meteogram.SESSION_KEY] = state
    else:
        state["_schedule_setup"] = True
    return state


def _product_keyboard() -> InlineKeyboardMarkup:
    """Put the most common schedule products first and keep labels short."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Метеограмма", callback_data="sched:product:meteogram"),
                InlineKeyboardButton("📈 Профиль", callback_data="sched:product:profile"),
            ],
            [
                InlineKeyboardButton("☁️ Облака", callback_data="sched:product:cloudgram"),
                InlineKeyboardButton("🟦 Срок × уровень", callback_data="sched:product:windgram"),
            ],
            [
                InlineKeyboardButton("🧾 Аэродиаграмма", callback_data="sched:product:aero"),
                InlineKeyboardButton("🗺️ Карта / анимация", callback_data="sched:product:map"),
            ],
            [InlineKeyboardButton("← Расписания", callback_data="sched:list")],
        ]
    )


def _quick_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🕒 В расписание", callback_data="sched:quick")]]
    )


def _valid_spec(spec: Any) -> bool:
    return (
        isinstance(spec, dict)
        and isinstance(spec.get("point"), dict)
        and bool(str(spec.get("product") or ""))
        and isinstance(spec.get("params"), dict)
    )


async def offer_schedule_for_result(
    message,
    context,
    spec: dict[str, object],
    user_id: int,
) -> bool:
    """Offer one-click schedule setup only when the user still has capacity."""

    if user_id <= 0 or not _valid_spec(spec) or _is_scheduled_message(message):
        return False
    try:
        items = schedules.schedule_store().list_for_user(user_id)
    except Exception:
        return False
    if len(items) >= schedules.MAX_SCHEDULES_PER_USER:
        context.user_data.pop(QUICK_SPEC_KEY, None)
        return False

    context.user_data[QUICK_SPEC_KEY] = {
        "product": str(spec["product"]),
        "point": dict(spec["point"]),
        "params": dict(spec["params"]),
    }
    await message.reply_text(
        "🕒 Повторять этот прогноз автоматически?",
        reply_markup=_quick_keyboard(),
    )
    return True


async def schedule_setup_input_guard(
    update: Update,
    context,
    namespace: dict[str, Any],
    *,
    location: bool = False,
) -> None:
    """Route schedule setup input strictly to the selected product.

    The generic bot text handler defaults to /profile. During schedule setup
    that fallback is forbidden: a selected meteogram must remain a meteogram.
    """

    product = _setup_product(context)
    if not product:
        return

    message = update.effective_message
    if message is None:
        raise ApplicationHandlerStop

    if product == "meteogram":
        import telegram_meteogram

        state = _ensure_meteogram_schedule_state(context)
        if state.get("step") == "point":
            if location:
                await telegram_meteogram.meteogram_location(update, context)
            else:
                await telegram_meteogram.meteogram_text(update, context)
        else:
            await message.reply_text("Продолжите настройку метеограммы кнопками выше.")
        raise ApplicationHandlerStop

    if product == "profile":
        if context.user_data.get("pending_profile"):
            await message.reply_text("Точка уже выбрана. Выберите срок профиля кнопками выше.")
            raise ApplicationHandlerStop
        handler = namespace["location_message"] if location else namespace["text_message"]
        await handler(update, context)
        raise ApplicationHandlerStop

    wizard_key = getattr(schedules, "PRODUCT_WIZARD_KEY", "product_wizard")
    state = context.user_data.get(wizard_key)
    if not isinstance(state, dict):
        _clear_product_setup(context)
        await message.reply_text(
            "Настройка продукта была сброшена. Откройте /schedule и выберите продукт заново."
        )
        raise ApplicationHandlerStop

    step = str(state.get("step") or "")
    if (location and step in {"await_point", "choose_place"}) or (
        not location and step == "await_point"
    ):
        handler = namespace["location_message"] if location else namespace["text_message"]
        await handler(update, context)
    else:
        await message.reply_text("Продолжите настройку выбранного продукта кнопками выше.")
    raise ApplicationHandlerStop


def register_input_guards(application, namespace: dict[str, Any]) -> None:
    application.add_handler(
        MessageHandler(
            filters.LOCATION,
            lambda update, context: schedule_setup_input_guard(
                update, context, namespace, location=True
            ),
        ),
        group=-11,
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            lambda update, context: schedule_setup_input_guard(
                update, context, namespace, location=False
            ),
        ),
        group=-11,
    )


def _manager_text_wrapper(original):
    def wrapped(user_id: int, store=None):
        text, items = original(user_id, store)
        if len(items) < schedules.MAX_SCHEDULES_PER_USER:
            text += (
                "\n\nБыстрее: сначала постройте нужный прогноз и нажмите "
                "«🕒 В расписание» — точка и параметры подставятся автоматически."
            )
        return text, items

    return wrapped


def _capture_result(user_id: int, product: str, success: bool) -> None:
    if user_id > 0:
        _LAST_RESULT[(user_id, str(product))] = bool(success)


def _pop_success(user_id: int, product: str) -> bool:
    return bool(_LAST_RESULT.pop((user_id, str(product)), False))


def _remember_spec(user_id: int, product: str, spec: dict[str, object]) -> None:
    if user_id > 0 and _valid_spec(spec):
        _LAST_SPEC[(user_id, str(product))] = spec


def _pop_spec(user_id: int, product: str) -> dict[str, object] | None:
    value = _LAST_SPEC.pop((user_id, str(product)), None)
    return value if _valid_spec(value) else None


def install(namespace: dict[str, Any]) -> None:
    """Patch handler functions before the Telegram Application is built."""

    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_clear_pending = namespace["_clear_pending"]

    def clear_pending(context):
        original_clear_pending(context)
        context.user_data.pop(PRODUCT_SETUP_KEY, None)
        context.user_data.pop(QUICK_SPEC_KEY, None)

    namespace["_clear_pending"] = clear_pending

    original_tracked_product = namespace["_tracked_product"]

    async def tracked_product(
        message,
        product: str,
        city: str | None,
        request_text: str | None,
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
        if not _is_scheduled_message(message):
            _capture_result(
                _message_user_id(message, user),
                product,
                result is not False,
            )
        return result

    namespace["_tracked_product"] = tracked_product

    original_tracked_profile = namespace["_tracked_run_profile"]

    async def tracked_profile(
        message,
        point,
        lead_hour: int,
        run=None,
        request_text: str | None = None,
        user=None,
    ):
        if _is_scheduled_message(message):
            return await original_tracked_profile(
                message,
                point,
                lead_hour,
                run,
                request_text=request_text,
                user=user,
            )
        user_id = _message_user_id(message, user)
        _LAST_SPEC.pop((user_id, "profile"), None)
        result = await original_tracked_profile(
            message,
            point,
            lead_hour,
            run,
            request_text=request_text,
            user=user,
        )
        if result:
            spec = schedules.schedule_spec_from_profile(
                {
                    "lat": float(point.lat),
                    "lon": float(point.lon),
                    "label": str(point.label),
                    "source": str(point.source),
                },
                int(lead_hour),
            )
            _remember_spec(user_id, "profile", spec)
        return result

    namespace["_tracked_run_profile"] = tracked_profile

    original_resolve_profile = namespace["resolve_profile_request"]

    async def resolve_profile_request(message, context, raw: str):
        user_id = _message_user_id(message)
        _LAST_SPEC.pop((user_id, "profile"), None)
        _LAST_RESULT.pop((user_id, "profile"), None)
        result = await original_resolve_profile(message, context, raw)
        spec = _pop_spec(user_id, "profile")
        if spec is not None and _pop_success(user_id, "profile"):
            await offer_schedule_for_result(message, context, spec, user_id)
        return result

    namespace["resolve_profile_request"] = resolve_profile_request

    original_lead_callback = namespace["lead_callback"]

    async def lead_callback(update, context):
        user_id = _user_id(update)
        _LAST_SPEC.pop((user_id, "profile"), None)
        _LAST_RESULT.pop((user_id, "profile"), None)
        await original_lead_callback(update, context)
        spec = _pop_spec(user_id, "profile")
        if spec is not None and _pop_success(user_id, "profile") and update.callback_query:
            await offer_schedule_for_result(
                update.callback_query.message,
                context,
                spec,
                user_id,
            )

    namespace["lead_callback"] = lead_callback

    original_place_callback = namespace["place_callback"]

    async def place_callback(update, context):
        user_id = _user_id(update)
        _LAST_SPEC.pop((user_id, "profile"), None)
        _LAST_RESULT.pop((user_id, "profile"), None)
        await original_place_callback(update, context)
        spec = _pop_spec(user_id, "profile")
        if spec is not None and _pop_success(user_id, "profile") and update.callback_query:
            await offer_schedule_for_result(
                update.callback_query.message,
                context,
                spec,
                user_id,
            )

    namespace["place_callback"] = place_callback

    original_product_wizard_callback = namespace["product_wizard_callback"]

    async def product_wizard_callback(update, context):
        query = update.callback_query
        data = query.data if query else ""
        state = context.user_data.get(getattr(schedules, "PRODUCT_WIZARD_KEY", "product_wizard"))
        spec = None
        product = ""
        user_id = _user_id(update)
        if (
            data == "wiz:run"
            and isinstance(state, dict)
            and not state.get("_schedule_setup")
        ):
            try:
                spec = schedules.schedule_spec_from_product_state(state)
                product = str(spec["product"])
                _LAST_RESULT.pop((user_id, product), None)
            except Exception:
                spec = None
        result = await original_product_wizard_callback(update, context)
        if (
            spec is not None
            and query is not None
            and _pop_success(user_id, product)
        ):
            await offer_schedule_for_result(query.message, context, spec, user_id)
        return result

    namespace["product_wizard_callback"] = product_wizard_callback

    # Preserve schedule intent around the reused product wizards.
    original_start_setup = schedules._start_product_setup

    async def start_product_setup(update, context, ns, product: str):
        _clear_nested_product_state(context)
        await original_start_setup(update, context, ns, product)
        _mark_product_setup(context, product)

    schedules._start_product_setup = start_product_setup

    original_product_interceptor = schedules.schedule_product_run_interceptor

    async def product_interceptor(update, context):
        try:
            return await original_product_interceptor(update, context)
        finally:
            _clear_product_setup(context)

    schedules.schedule_product_run_interceptor = product_interceptor

    original_profile_interceptor = schedules.schedule_profile_lead_interceptor

    async def profile_interceptor(update, context):
        try:
            return await original_profile_interceptor(update, context)
        finally:
            _clear_product_setup(context)

    schedules.schedule_profile_lead_interceptor = profile_interceptor

    original_meteogram_run_interceptor = schedules.schedule_meteogram_run_interceptor

    async def meteogram_run_interceptor(update, context):
        try:
            return await original_meteogram_run_interceptor(update, context)
        finally:
            _clear_product_setup(context)

    schedules.schedule_meteogram_run_interceptor = meteogram_run_interceptor

    original_schedule_callback = schedules.schedule_callback

    async def schedule_callback(update, context, ns):
        query = update.callback_query
        data = (query.data or "") if query else ""

        if data == "sched:quick":
            if query is None:
                raise ApplicationHandlerStop
            await query.answer()
            user_id = _user_id(update)
            spec = context.user_data.pop(QUICK_SPEC_KEY, None)
            if not _valid_spec(spec):
                await schedules._show_manager(query, user_id)
                raise ApplicationHandlerStop
            try:
                if len(schedules.schedule_store().list_for_user(user_id)) >= schedules.MAX_SCHEDULES_PER_USER:
                    await schedules._show_manager(query, user_id)
                    raise ApplicationHandlerStop
            except schedules.ScheduleError:
                await schedules._show_manager(query, user_id)
                raise ApplicationHandlerStop
            _clear_nested_product_state(context)
            await schedules._begin_timing(query, context, dict(spec))
            raise ApplicationHandlerStop

        if data in {
            "home:schedule",
            "sched:list",
            "sched:home",
            "sched:new",
            "sched:abort",
        }:
            _clear_nested_product_state(context)
        elif data.startswith("sched:product:"):
            _clear_nested_product_state(context)

        return await original_schedule_callback(update, context, ns)

    schedules.schedule_callback = schedule_callback
    schedules._product_keyboard = _product_keyboard
    schedules._manager_text = _manager_text_wrapper(schedules._manager_text)

    # Meteogram live runs can preserve their complete schedule specification.
    import telegram_meteogram

    original_meteogram_run = telegram_meteogram._run_product

    async def meteogram_run(
        message,
        point,
        request,
        user=None,
        *,
        output_format: str = "png",
    ):
        if _is_scheduled_message(message):
            return await original_meteogram_run(
                message,
                point,
                request,
                user,
                output_format=output_format,
            )
        user_id = _message_user_id(message, user)
        _LAST_SPEC.pop((user_id, "meteogram"), None)
        _LAST_RESULT.pop((user_id, "meteogram"), None)
        result = await original_meteogram_run(
            message,
            point,
            request,
            user,
            output_format=output_format,
        )
        _capture_result(user_id, "meteogram", result is not False)
        if result:
            _remember_spec(
                user_id,
                "meteogram",
                {
                    "product": "meteogram",
                    "point": {
                        "lat": float(point.lat),
                        "lon": float(point.lon),
                        "label": str(point.label),
                        "source": str(point.source),
                    },
                    "params": {
                        "source_id": str(request.source_id),
                        "days": int(request.days),
                        "output_format": str(output_format).lower(),
                    },
                },
            )
        return result

    telegram_meteogram._run_product = meteogram_run

    original_meteogram_callback = telegram_meteogram.meteogram_callback

    async def meteogram_callback(update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = _user_id(update)
        is_live_run = False
        if data == "meteo:run":
            state = context.user_data.get(telegram_meteogram.SESSION_KEY)
            is_live_run = isinstance(state, dict) and not state.get("_schedule_setup")
            if is_live_run:
                _LAST_SPEC.pop((user_id, "meteogram"), None)
                _LAST_RESULT.pop((user_id, "meteogram"), None)

        stopped = False
        try:
            result = await original_meteogram_callback(update, context)
        except ApplicationHandlerStop:
            stopped = True
            result = None

        if is_live_run and query is not None:
            spec = _pop_spec(user_id, "meteogram")
            if spec is not None and _pop_success(user_id, "meteogram"):
                await offer_schedule_for_result(query.message, context, spec, user_id)

        if stopped:
            raise ApplicationHandlerStop
        return result

    telegram_meteogram.meteogram_callback = meteogram_callback

    original_meteogram_command = telegram_meteogram.meteogram_command

    async def meteogram_command(update, context):
        user_id = _user_id(update)
        _LAST_SPEC.pop((user_id, "meteogram"), None)
        _LAST_RESULT.pop((user_id, "meteogram"), None)
        result = await original_meteogram_command(update, context)
        spec = _pop_spec(user_id, "meteogram")
        if spec is not None and _pop_success(user_id, "meteogram") and update.effective_message:
            await offer_schedule_for_result(
                update.effective_message,
                context,
                spec,
                user_id,
            )
        return result

    telegram_meteogram.meteogram_command = meteogram_command
