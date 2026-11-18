"""Telegram transport for the regional icing router shared with MAX and VK."""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from messenger.callback_codec import CallbackCodecError, decode_callback, encode_callback
from messenger.contracts import Location, NormalizedEvent, PlatformMessage
from messenger.runtime_resources import get_runtime_resources
from telegram_file_send import reply_png_file
from telegram_ui import native_callback_payload

SESSION_KEY = "icing_wizard"
LOCATION_KEYBOARD_KEY = "icing_location_keyboard"
_POINT_STEPS = {"await_point", "point_search", "choose_point"}
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
    if any(button.action == "request_location" for row in keyboard.rows for button in row):
        rows = []
        for row in keyboard.rows:
            native_row = []
            for button in row:
                if button.action == "request_location":
                    native_row.append(KeyboardButton(button.text, request_location=True))
                elif button.action == "text":
                    native_row.append(KeyboardButton(button.text))
                elif button.action == "callback":
                    try:
                        action = decode_callback(button.payload).action
                    except CallbackCodecError:
                        continue
                    label = ("Меню" if native_callback_payload(button.payload) == "prefs:home"
                             else {"c": "Назад к карте", "d": "Убрать пункт"}.get(action))
                    if label:
                        native_row.append(KeyboardButton(label))
            if native_row:
                rows.append(native_row)
        rows.append([KeyboardButton("Отмена выбора пункта")])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True,
                                   selective=True, is_persistent=False,
                                   input_field_placeholder="Город или широта, долгота")
    rows = [[InlineKeyboardButton(button.text, callback_data=native_callback_payload(button.payload)) if button.action == "callback"
             else InlineKeyboardButton(button.text, url=button.url) for button in row
             if button.action in {"callback", "link"}]
            for row in keyboard.rows]
    return InlineKeyboardMarkup([row for row in rows if row])


class TelegramIcingGateway:
    platform = "telegram"

    def __init__(self, bot, user_data=None):
        self.bot = bot
        self.user_data = user_data if user_data is not None else {}

    @staticmethod
    def _message(chat_id, result):
        return PlatformMessage("telegram", str(chat_id), str(result.message_id))

    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        markup = telegram_markup(keyboard)
        if keyboard is not None and not isinstance(markup, ReplyKeyboardMarkup):
            await self.remove_location_keyboard(chat_id)
        result = await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=parse_mode)
        if isinstance(markup, ReplyKeyboardMarkup):
            self.user_data[LOCATION_KEYBOARD_KEY] = True
        return self._message(chat_id, result)

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        markup = telegram_markup(keyboard)
        if isinstance(markup, ReplyKeyboardMarkup):
            # Telegram can attach a native location request only to a new message.
            return await self.send_text(chat_id, text, keyboard=keyboard, parse_mode=parse_mode)
        if keyboard is not None:
            await self.remove_location_keyboard(chat_id)
        await self.bot.edit_message_text(chat_id=chat_id, message_id=int(message_id), text=text,
                                         reply_markup=markup, parse_mode=parse_mode)
        return PlatformMessage("telegram", str(chat_id), str(message_id))

    async def remove_location_keyboard(self, chat_id):
        if not self.user_data.get(LOCATION_KEYBOARD_KEY):
            return
        try:
            marker = await self.bot.send_message(chat_id=chat_id, text="\u2063", reply_markup=ReplyKeyboardRemove())
        except Exception:
            return
        self.user_data.pop(LOCATION_KEYBOARD_KEY, None)
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=marker.message_id)
        except Exception:
            pass

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
    native_location = getattr(message, "location", None) if not callback else None
    location = Location(float(native_location.latitude), float(native_location.longitude)) if native_location else None
    command = text.split()[0].lstrip("/").split("@")[0].lower() if text and text.startswith("/") and not callback else None
    payload = callback.data if callback else None
    if payload == "home:icing":
        payload = encode_callback("product", "open", "icing")
    return NormalizedEvent(
        "telegram", str(update.update_id), "CALLBACK" if callback else "LOCATION" if location else "COMMAND" if command else "TEXT",
        str(user.id), str(chat.id), message_id=str(message.message_id) if message else None,
        text=text, command=command, callback_payload=payload,
        callback_id=callback.id if callback else None,
        location=location,
    )


def _awaiting_point(router, event):
    state = router.sessions.get(event.platform, event.user_id, event.chat_id) if router else None
    return state is not None and state.product == "icing" and state.step in _POINT_STEPS


async def icing_update(update, context):
    event = _normalized(update)
    if event is None:
        return
    if event.event_type == "TEXT" and not context.user_data.get(SESSION_KEY):
        return
    router = None
    if event.event_type == "LOCATION":
        router = _ROUTER
        if router is None and context.user_data.get(SESSION_KEY):
            router = get_router()
        if not _awaiting_point(router, event):
            return
    if event.command == "icing" or event.callback_payload == encode_callback("product", "open", "icing"):
        # Leave previous Telegram point/parameter wizards before opening a regional product.
        import telegram_bot
        telegram_bot._clear_pending(context)
    context.user_data[SESSION_KEY] = True
    router = router or get_router()
    gateway = TelegramIcingGateway(context.bot, context.user_data)
    if event.event_type == "TEXT" and (event.text or "").strip() == "Меню" and _awaiting_point(router, event):
        # A reply keyboard has no callback payload; reuse the complete native home.
        import telegram_bot
        router.sessions.clear(event.platform, event.user_id, event.chat_id)
        context.user_data.pop(SESSION_KEY, None)
        context.user_data.pop(LOCATION_KEYBOARD_KEY, None)
        await telegram_bot.start(update, context)
        raise ApplicationHandlerStop
    await router.handle(event, gateway)
    state = router.sessions.get(event.platform, event.user_id, event.chat_id)
    if not _awaiting_point(router, event):
        await gateway.remove_location_keyboard(event.chat_id)
    if (state is None or state.product != "icing") and router._icing_key(event) not in router.icing_jobs:
        context.user_data.pop(SESSION_KEY, None)
    raise ApplicationHandlerStop


async def _command_guard(update, context):
    event = _normalized(update)
    if event is None:
        return
    # Do not create any router for unrelated commands on a fresh Telegram session.
    router = _ROUTER
    if event.location is not None and _awaiting_point(router, event):
        return
    active = bool(context.user_data.get(SESSION_KEY)) or (
        router is not None and router._icing_key(event) in router.icing_jobs)
    if not active or event.command == "icing":
        return
    if event.command in {"cancel", "status"}:
        gateway = TelegramIcingGateway(context.bot, context.user_data)
        await get_router().handle(event, gateway)
        if event.command == "cancel":
            context.user_data.pop(SESSION_KEY, None)
            await gateway.remove_location_keyboard(event.chat_id)
        raise ApplicationHandlerStop
    context.user_data.pop(SESSION_KEY, None)
    state = router.sessions.get(event.platform, event.user_id, event.chat_id) if router else None
    if state is not None and state.product == "icing":
        router.sessions.clear(event.platform, event.user_id, event.chat_id)
    await TelegramIcingGateway(context.bot, context.user_data).remove_location_keyboard(event.chat_id)


async def _navigation_guard(update, context):
    payload = getattr(update.callback_query, "data", "") or ""
    if payload == "home:icing" or payload.startswith("v1|icing|") or payload == encode_callback("product", "open", "icing"):
        return
    if not context.user_data.pop(SESSION_KEY, None):
        return
    event = _normalized(update)
    if event:
        await TelegramIcingGateway(context.bot, context.user_data).remove_location_keyboard(event.chat_id)
    if event and _ROUTER is not None:
        state = _ROUTER.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is not None and state.product == "icing":
            _ROUTER.sessions.clear(event.platform, event.user_id, event.chat_id)


def register(application):
    application.add_handler(MessageHandler(filters.COMMAND | filters.LOCATION, _command_guard), group=-31)
    application.add_handler(CallbackQueryHandler(_navigation_guard), group=-31)
    application.add_handler(CommandHandler("icing", icing_update), group=-30)
    application.add_handler(CallbackQueryHandler(icing_update, pattern=r"^(home:icing$|v1\|icing\||v1\|product\|open\|icing$)"), group=-30)
    application.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.LOCATION, icing_update), group=-30)
    previous_shutdown = application.post_shutdown

    async def shutdown(app):
        if _ROUTER is not None:
            await _ROUTER.shutdown_icing()
        if previous_shutdown:
            await previous_shutdown(app)
    application.post_shutdown = shutdown
