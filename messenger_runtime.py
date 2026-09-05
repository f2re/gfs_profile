from __future__ import annotations

"""Production single-process Telegram + MAX + VK + web/API runtime.

Each messenger is optional and failure-isolated.  The FastAPI runtime remains
ready for healthy platforms and web/API even when another provider is disabled
or cannot start.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from telegram import Update

import app as legacy_web_module
from messenger.cloudgram_router import CloudgramMessengerRouter
from messenger.platform_config import PlatformStatus, platform_statuses
from messenger.runtime_resources import RuntimeResources, get_runtime_resources
from messenger.webhooks import MessengerWebhookService

LOG = logging.getLogger(__name__)
RESOURCES = get_runtime_resources()
ROUTER = RESOURCES.configure_router(CloudgramMessengerRouter.default())
SERVICE = MessengerWebhookService.from_env(router=ROUTER)


def configure_process_resources(resources: RuntimeResources = RESOURCES) -> None:
    """Bind every in-process frontend to the same capacity gates."""

    import telegram_bot
    import telegram_meteogram

    telegram_bot.GFS_SEMAPHORE = resources.gfs_semaphore
    telegram_bot.GEOCODE_SEMAPHORE = resources.geocode_semaphore
    telegram_bot.MAX_CONCURRENT_GFS = resources.gfs_limit
    telegram_bot.MAX_CONCURRENT_GEOCODE = resources.geocode_limit

    telegram_meteogram.METEOGRAM_SEMAPHORE = resources.meteogram_semaphore
    telegram_meteogram.MAX_CONCURRENT_METEOGRAM = resources.meteogram_limit
    telegram_meteogram.search_location_candidates = resources.wrap_blocking_geocode(
        telegram_meteogram.search_location_candidates
    )

    legacy_web_module.build_profile = resources.wrap_blocking_gfs(
        legacy_web_module.build_profile
    )


configure_process_resources()
legacy_web_app = legacy_web_module.app


def _status_dict(status: PlatformStatus) -> dict[str, object]:
    return status.as_dict()


def _runtime_degraded(name: str, reason: str) -> dict[str, object]:
    return {
        "requested": True,
        "ready": False,
        "state": "degraded",
        "reason": reason,
    }


async def _cleanup_partial_telegram(application: Any | None) -> None:
    if application is None:
        return
    updater = getattr(application, "updater", None)
    try:
        if updater is not None and bool(getattr(updater, "running", False)):
            await updater.stop()
    except Exception:
        LOG.exception("Telegram updater cleanup failed")
    try:
        if bool(getattr(application, "running", False)):
            await application.stop()
    except Exception:
        LOG.exception("Telegram application cleanup failed")
    try:
        await application.shutdown()
    except Exception:
        LOG.exception("Telegram shutdown after failed startup failed")


async def _start_telegram(app: FastAPI, config: PlatformStatus) -> Any | None:
    if not config.ready:
        return None

    import telegram_bot

    application = None
    try:
        application = telegram_bot.build_application()
        await application.initialize()
        if application.post_init:
            await application.post_init(application)
        if application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await application.start()
        app.state.platform_runtime["telegram"] = {
            "requested": True,
            "ready": True,
            "state": "ready",
            "reason": "",
        }
        LOG.info("Telegram runtime started")
        return application
    except Exception as exc:
        LOG.exception("Telegram startup failed; MAX/VK/web continue")
        app.state.platform_runtime["telegram"] = _runtime_degraded("telegram", f"startup: {exc}")
        await _cleanup_partial_telegram(application)
        return None


async def _stop_telegram(application: Any | None) -> None:
    if application is None:
        return
    updater = getattr(application, "updater", None)
    if updater is not None and bool(getattr(updater, "running", False)):
        await updater.stop()
    if bool(getattr(application, "running", False)):
        await application.stop()
        if application.post_stop:
            await application.post_stop(application)
    await application.shutdown()
    if application.post_shutdown:
        await application.post_shutdown(application)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configs = platform_statuses()
    app.state.runtime_ready = False
    app.state.platform_runtime = {name: _status_dict(status) for name, status in configs.items()}

    # MAX/VK gateways are built independently by MessengerWebhookService. A bad
    # platform simply has no gateway and its own webhook returns 503.
    app.state.platform_runtime["max"] = _status_dict(SERVICE.platform_status["max"])
    app.state.platform_runtime["vk"] = _status_dict(SERVICE.platform_status["vk"])

    telegram_application = await _start_telegram(app, configs["telegram"])
    app.state.telegram_application = telegram_application

    # Readiness means the shared HTTP/runtime infrastructure is usable. Platform
    # degradation is exposed in /health but must not make healthy siblings fail.
    app.state.runtime_ready = True
    try:
        yield
    finally:
        app.state.runtime_ready = False
        await SERVICE.tasks.shutdown()
        try:
            await _stop_telegram(telegram_application)
        except Exception:
            LOG.exception("Telegram shutdown failed; runtime shutdown continues")


app = FastAPI(title="GFS Profile Messenger Runtime", lifespan=lifespan)


def _current_platform_runtime() -> dict[str, dict[str, object]]:
    value = getattr(app.state, "platform_runtime", None)
    if isinstance(value, dict):
        return value
    return {name: status.as_dict() for name, status in platform_statuses().items()}


@app.get("/health")
async def health() -> dict[str, object]:
    states = _current_platform_runtime()
    requested_degraded = any(
        bool(item.get("requested")) and item.get("state") != "ready"
        for item in states.values()
    )
    return {
        "status": "degraded" if requested_degraded else "ok",
        "runtime": "multi-messenger",
        # Backwards-compatible booleans plus detailed independent state.
        "platforms": {name: item.get("state") == "ready" for name, item in states.items()},
        "platform_status": states,
        "products": ["profile", "aero", "windgram", "cloudgram"],
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
