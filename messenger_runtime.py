from __future__ import annotations

"""Single-process Telegram + MAX + VK runtime candidate.

The current deploy still starts telegram_bot.py unless the multi-messenger
runtime flag is enabled. MAX/VK use the same persistent recipe service and the
same common profile/aerological product services as Telegram.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from telegram import Update

from app import app as legacy_web_app
from messenger.aero_router import AeroMessengerRouter
from messenger.webhooks import MessengerWebhookService
from telegram_bot import build_application

SERVICE = MessengerWebhookService.from_env(router=AeroMessengerRouter.default())


@asynccontextmanager
async def lifespan(app: FastAPI):
    telegram_application = build_application()
    await telegram_application.initialize()
    if telegram_application.post_init:
        await telegram_application.post_init(telegram_application)
    if telegram_application.updater is None:
        raise RuntimeError("Telegram updater is unavailable")
    await telegram_application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await telegram_application.start()
    app.state.telegram_application = telegram_application
    try:
        yield
    finally:
        await SERVICE.tasks.shutdown()
        await telegram_application.updater.stop()
        await telegram_application.stop()
        if telegram_application.post_stop:
            await telegram_application.post_stop(telegram_application)
        await telegram_application.shutdown()
        if telegram_application.post_shutdown:
            await telegram_application.post_shutdown(telegram_application)


app = FastAPI(title="GFS Profile Messenger Runtime", lifespan=lifespan)
app.include_router(SERVICE.api_router())
app.mount("/", legacy_web_app)
