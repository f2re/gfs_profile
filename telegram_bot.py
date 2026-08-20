from __future__ import annotations

# Keep the established telegram_bot module namespace intact for existing tests,
# monkeypatches and deploy entrypoints, while moving the previous implementation
# to a stable source file and composing optional products here.
from pathlib import Path

_ENTRYPOINT_MODULE_NAME = __name__
_CORE_PATH = Path(__file__).with_name("telegram_bot_core.py")

try:
    __name__ = "telegram_bot_embedded_core"
    exec(compile(_CORE_PATH.read_text(encoding="utf-8"), str(_CORE_PATH), "exec"), globals(), globals())
finally:
    __name__ = _ENTRYPOINT_MODULE_NAME

import admin_product_policy  # noqa: E402
import aero_single_mode  # noqa: E402
import route_profile_contract  # noqa: F401,E402
import route_profile_vertical_policy  # noqa: F401,E402
import route_profile_rendering  # noqa: F401,E402
import telegram_concise_ux  # noqa: E402
import telegram_result_copy  # noqa: E402
import meteorological_policy  # noqa: E402

# Presentation and product policies are applied before handlers are built.
telegram_concise_ux.install(globals())
telegram_result_copy.install()
aero_single_mode.install(globals())
admin_product_policy.install(globals())
meteorological_policy.install(globals())

# All product switches, /start and /cancel must also discard the independent
# meteogram wizard, otherwise a later plain-text message could resume stale state.
_core_clear_pending = _clear_pending


def _clear_pending(context):
    _core_clear_pending(context)
    context.user_data.pop("meteogram_wizard", None)
    context.user_data.pop("schedule_wizard", None)
    context.user_data.pop("schedule_profile_setup", None)


# A callback message is authored by the bot, so message.from_user cannot identify
# the person who opened a product from the inline home menu. Capture the actual
# callback user for the point-selection keyboard and persistent defaults.
_concise_start_product_wizard = _start_product_wizard


async def _start_product_wizard(message, context, state):
    user_id = int(context.user_data.pop("_ux_home_user_id", 0) or 0)
    if user_id <= 0:
        await _concise_start_product_wizard(message, context, state)
        return
    _clear_pending(context)
    context.user_data[PRODUCT_WIZARD_KEY] = state
    await message.reply_text(
        telegram_concise_ux.point_prompt_text(state),
        reply_markup=telegram_concise_ux.point_keyboard(get_recent_locations(user_id)),
    )


from telegram_route import register_route_handlers
from telegram_meteogram import register_meteogram_handlers
from telegram_schedules import register_schedule_handlers
import telegram_schedule_ux  # noqa: E402

# Install the schedule guard first. Personal UX then wraps the final product
# runners, so scheduled deliveries remain snapshots and never change defaults.
telegram_schedule_ux.install(globals())

import telegram_personal_ux  # noqa: E402

telegram_personal_ux.install(globals())

import telegram_personal_wizard_policy  # noqa: E402

telegram_personal_wizard_policy.install()

_core_build_application = build_application


def build_application():
    application = _core_build_application()

    async def capture_home_user(update, context):
        if update.effective_user:
            context.user_data["_ux_home_user_id"] = int(update.effective_user.id)

    application.add_handler(
        CallbackQueryHandler(
            capture_home_user,
            pattern=r"^home:(aero|windgram|cloudgram|map)$",
        ),
        group=-3,
    )
    telegram_personal_ux.register(application, globals())
    telegram_concise_ux.register(application, globals())
    register_meteogram_handlers(application)
    telegram_schedule_ux.register_input_guards(application, globals())
    register_schedule_handlers(application, globals())
    register_route_handlers(
        application,
        gfs_semaphore=GFS_SEMAPHORE,
        geocode_semaphore=GEOCODE_SEMAPHORE,
    )
    aero_single_mode.configure_application(application)
    return application


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if _ENTRYPOINT_MODULE_NAME == "__main__":
    main()
