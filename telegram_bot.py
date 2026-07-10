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

# Apply the simple-mode visual policy before telegram_route imports the renderer.
# This fills cloud, icing and turbulence contours with repeated symbols.
import route_profile_simple_overlay  # noqa: F401,E402
from telegram_route import register_route_handlers

_core_build_application = build_application


def build_application():
    application = _core_build_application()
    register_route_handlers(
        application,
        gfs_semaphore=GFS_SEMAPHORE,
        geocode_semaphore=GEOCODE_SEMAPHORE,
    )
    return application


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if _ENTRYPOINT_MODULE_NAME == "__main__":
    main()
