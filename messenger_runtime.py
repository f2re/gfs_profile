from __future__ import annotations

"""Production single-process Telegram + MAX + VK + web/API runtime."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from telegram import Update

import app as legacy_web_module
from messenger.aero_router import AeroMessengerRouter
from messenger.runtime_resources import RuntimeResources, get_runtime_resources
from messenger.webhooks import MessengerWebhookService

RESOURCES = get_runtime_resources()
ROUTER = RESOURCES.configure_router(AeroMessengerRouter.default())
SERVICE = MessengerWebhookService.from_env(router=ROUTER)


def configure_process_resources(resources: RuntimeResources = RESOURCES) -> None:
    """Bind every in-process frontend to the same capacity gates.

    Telegram historically created its semaphores in module globals and the web
    API called ``build_profile`` synchronously.  Keeping those public globals
    makes the migration backwards-compatible while the actual permits now come
    from one process-wide resource pool.
    """

    import telegram_bot
    import telegram_meteogram

    telegram_bot.GFS_SEMAPHORE = resources.gfs_semaphore
    telegram_bot.GEOCODE_SEMAPHORE = resources.geocode_semaphore
    telegram_bot.MAX_CONCURRENT_GFS = resources.gfs_limit
    telegram_bot.MAX_CONCURRENT_GEOCODE = resources.geocode_limit

    telegram_meteogram.METEOGRAM_SEMAPHORE = resources.meteogram_semaphore
    telegram_meteogram.MAX_CONCURRENT_METEOGRAM = resources.meteogram_limit
    # Meteogram point resolution did not historically receive the core
    # GEOCODE_SEMAPHORE, so guard its blocking geocoder with the same gate.
    telegram_meteogram.search_location_candidates = resources.wrap_blocking_geocode(
        telegram_meteogram.search_location_candidates
    )

    # The mounted web/API uses the same GFS budget as all messenger requests.
    legacy_web_module.build_profile = resources.wrap_blocking_gfs(
        legacy_web_module.build_profile
    )


configure_process_resources()
legacy_web_app = legacy_web_module.app


@asynccontextmanager
async def lifespan(app: FastAPI):
    import telegram_bot

    app.state.runtime_ready = False
    telegram_application = telegram_bot.build_application()
    await telegram_application.initialize()
    if telegram_application.post_init:
        await telegram_application.post_init(telegram_application)
    if telegram_application.updater is None:
        raise RuntimeError("Telegram updater is unavailable")
    await telegram_application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await telegram_application.start()
    app.state.telegram_application = telegram_application
    app.state.runtime_ready = True
    try:
        yield
    finally:
        app.state.runtime_ready = False
        await SERVICE.tasks.shutdown()
        await telegram_application.updater.stop()
        await telegram_application.stop()
        if telegram_application.post_stop:
            await telegram_application.post_stop(telegram_application)
        await telegram_application.shutdown()
        if telegram_application.post_shutdown:
            await telegram_application.post_shutdown(telegram_application)


app = FastAPI(title="GFS Profile Messenger Runtime", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "runtime": "multi-messenger",
        "platforms": {
            "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip()),
            "max": SERVICE.max_gateway is not None,
            "vk": SERVICE.vk_gateway is not None,
        },
        "resources": RESOURCES.snapshot(),
    }


@app.get("/ready")
async def ready():
    if not bool(getattr(app.state, "runtime_ready", False)):
        return JSONResponse(
            status_code=503,
            content={"status": "starting", "runtime": "multi-messenger"},
        )
    return await health()


app.include_router(SERVICE.api_router())
app.mount("/", legacy_web_app)
