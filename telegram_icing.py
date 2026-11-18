"""Telegram transport for the regional icing router shared with MAX and VK."""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from messenger.callback_codec import encode_callback
from messenger.contracts import NormalizedEvent, PlatformMessage
from messenger.runtime_resources import get_runtime_resources
from telegram_file_send import reply_png_file
from telegram_ui import native_callback_payload

SESSION_KEY = "icing_wizard"
_ROUTER = None


def set_router(router):
    global _ROUTER
    _ROUTER = router


def get_router():
    global _ROUTER
    if _ROUTER is None:
        from messenger.icing_router import IcingMessengerRouter
        _ROUTER = get_runtime_resources().configure_router(IcingMessengerRouter.default())
    return _ROUTER


def telegram_markup(keyboard):
    if keyboard is None:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(button.text, callback_data=native_callback_payload(button.payload)) if button.action == "callback"
         else InlineKeyboardButton(button.text, url=button.url) for button in row
         if button.action in {"callback", "link"}]
        for row in keyboard.rows
    ])


class TelegramIcingGateway:
    platform = "telegram"

    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _message(chat_id, result):
        return PlatformMessage("telegram", str(chat_id), str(result.message_id))

    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        result = await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=telegram_markup(keyboard), parse_mode=parse_mode)
        return self._message(chat_id, result)

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        await self.bot.edit_message_text(chat_id=chat_id, message_id=int(message_id), text=text,
                                         reply_markup=telegram_markup(keyboard), parse_mode=parse_mode)
        return PlatformMessage("telegram", str(chat_id), str(message_id))

    async def send_image(self, chat_id, path, *, caption=""):
        message = SimpleNamespace(reply_photo=partial(self.bot.send_photo, chat_id=chat_id),
                                  reply_document=partial(self.bot.send_document, chat_id=chat_id))
        await reply_png_file(message, path, caption=caption[:1000])
        return PlatformMessage("telegram", str(chat_id), "")

    async def send_file(self, chat_id, path, *, caption="", filename=None):
        with path.open("rb") as handle:
            result = await self.bot.send_document(chat_id=chat_id, document=handle,
                filename=filename or path.name, caption=caption[:1000], read_timeout=120, write_timeout=120)
        return self._message(chat_id, result)

    async def answer_callback(self, event, *, text=None):
        if event.callback_id:
            await self.bot.answer_callback_query(callback_query_id=event.callback_id, text=text)


def _normalized(update):
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None:
        return None
    message, callback = update.effective_message, update.callback_query
    text = getattr(message, "text", None)
    command = text.split()[0].lstrip("/").split("@")[0].lower() if text and text.startswith("/") and not callback else None
    payload = callback.data if callback else None
    if payload == "home:icing":
        payload = encode_callback("product", "open", "icing")
    return NormalizedEvent(
        "telegram", str(update.update_id), "CALLBACK" if callback else "COMMAND" if command else "TEXT",
        str(user.id), str(chat.id), message_id=str(message.message_id) if message else None,
        text=text, command=command, callback_payload=payload,
        callback_id=callback.id if callback else None,
    )


async def icing_update(update, context):
    event = _normalized(update)
    if event is None:
        return
    if event.event_type == "TEXT" and not context.user_data.get(SESSION_KEY):
        return
    if event.command == "icing" or event.callback_payload == encode_callback("product", "open", "icing"):
        # Leave previous Telegram point/parameter wizards before opening a regional product.
        import telegram_bot
        telegram_bot._clear_pending(context)
    context.user_data[SESSION_KEY] = True
    router = get_router()
    await router.handle(event, TelegramIcingGateway(context.bot))
    state = router.sessions.get(event.platform, event.user_id, event.chat_id)
    if (state is None or state.product != "icing") and router._icing_key(event) not in router.icing_jobs:
        context.user_data.pop(SESSION_KEY, None)
    raise ApplicationHandlerStop


async def _command_guard(update, context):
    event = _normalized(update)
    if event is None:
        return
    # Do not create any router for unrelated commands on a fresh Telegram session.
    router = _ROUTER
    active = bool(context.user_data.get(SESSION_KEY)) or (
        router is not None and router._icing_key(event) in router.icing_jobs)
    if not active or event.command == "icing":
        return
    if event.command in {"cancel", "status"}:
        await get_router().handle(event, TelegramIcingGateway(context.bot))
        if event.command == "cancel":
            context.user_data.pop(SESSION_KEY, None)
        raise ApplicationHandlerStop
    context.user_data.pop(SESSION_KEY, None)
    state = router.sessions.get(event.platform, event.user_id, event.chat_id) if router else None
    if state is not None and state.product == "icing":
        router.sessions.clear(event.platform, event.user_id, event.chat_id)


async def _navigation_guard(update, context):
    payload = getattr(update.callback_query, "data", "") or ""
    if payload == "home:icing" or payload.startswith("v1|icing|") or payload == encode_callback("product", "open", "icing"):
        return
    if not context.user_data.pop(SESSION_KEY, None):
        return
    event = _normalized(update)
    if event and _ROUTER is not None:
        state = _ROUTER.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is not None and state.product == "icing":
            _ROUTER.sessions.clear(event.platform, event.user_id, event.chat_id)


def register(application):
    application.add_handler(MessageHandler(filters.COMMAND | filters.LOCATION, _command_guard), group=-31)
    application.add_handler(CallbackQueryHandler(_navigation_guard), group=-31)
    application.add_handler(CommandHandler("icing", icing_update), group=-30)
    application.add_handler(CallbackQueryHandler(icing_update, pattern=r"^(home:icing$|v1\|icing\||v1\|product\|open\|icing$)"), group=-30)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, icing_update), group=-30)
    previous_shutdown = application.post_shutdown

    async def shutdown(app):
        if _ROUTER is not None:
            await _ROUTER.shutdown_icing()
        if previous_shutdown:
            await previous_shutdown(app)
    application.post_shutdown = shutdown
