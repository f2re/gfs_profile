from __future__ import annotations

# Composition entrypoint: the existing single-process bot implementation remains
# in telegram_bot_core.py; optional products are registered here.
import telegram_bot_core as _core

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

from telegram_route import register_route_handlers


def build_application():
    application = _core.build_application()
    register_route_handlers(
        application,
        gfs_semaphore=_core.GFS_SEMAPHORE,
        geocode_semaphore=_core.GEOCODE_SEMAPHORE,
    )
    return application


def main() -> None:
    build_application().run_polling(allowed_updates=_core.Update.ALL_TYPES)


if __name__ == "__main__":
    main()
