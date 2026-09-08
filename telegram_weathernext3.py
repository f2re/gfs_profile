"""Thin Telegram transport for the same WN3 router used by MAX and VK."""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from messenger.callback_codec import decode_callback, encode_callback
from messenger.contracts import Location, NormalizedEvent, PlatformMessage
from messenger.runtime_resources import get_runtime_resources
from messenger.weathernext3_router import WeatherNext3MessengerRouter, _card_keyboard as common_keyboard
from messenger.weathernext3_service import DEFAULT_WN3_PARAMS, normalize_wn3_params
from telegram_file_send import reply_png_file

SESSION_KEY = 'weathernext3_wizard'
_RESOURCES = get_runtime_resources()
WN3_SEMAPHORE = _RESOURCES.weathernext3_semaphore
WN3_MAX_CONCURRENT = _RESOURCES.weathernext3_limit
_INSTALLED = False
_ROUTER = None


def set_router(router):
    global _ROUTER
    _ROUTER = router


def get_router():
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = get_runtime_resources().configure_router(WeatherNext3MessengerRouter.default())
    return _ROUTER


def telegram_markup(keyboard):
    if keyboard is None:
        return None
    if any(button.action == 'request_location' for row in keyboard.rows for button in row):
        return ReplyKeyboardMarkup([[KeyboardButton('📍 Геолокация', request_location=True)], [KeyboardButton('✖ Отмена')]],
                                   resize_keyboard=True, one_time_keyboard=True)
    rows = []
    for row in keyboard.rows:
        buttons = []
        for button in row:
            if button.action == 'callback':
                buttons.append(InlineKeyboardButton(button.text, callback_data=button.payload))
            elif button.action == 'link':
                buttons.append(InlineKeyboardButton(button.text, url=button.url))
        if buttons:
            rows.append(buttons)
    return InlineKeyboardMarkup(rows)


class TelegramGateway:
    platform = 'telegram'

    def __init__(self, bot):
        self.bot = bot

    def remember_point(self, user_id, point):
        from telegram_user_state import remember_location
        remember_location(int(user_id), point, activate=True)

    def _message(self, chat, response):
        return PlatformMessage(self.platform, str(chat), str(response.message_id))

    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        response = await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=telegram_markup(keyboard), parse_mode=parse_mode)
        return self._message(chat_id, response)

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        await self.bot.edit_message_text(chat_id=chat_id, message_id=int(message_id), text=text,
                                         reply_markup=telegram_markup(keyboard), parse_mode=parse_mode)
        return PlatformMessage(self.platform, str(chat_id), str(message_id))

    async def send_image(self, chat_id, path, *, caption=''):
        message = SimpleNamespace(reply_photo=partial(self.bot.send_photo, chat_id=chat_id),
                                  reply_document=partial(self.bot.send_document, chat_id=chat_id))
        await reply_png_file(message, path, caption=caption[:1000])
        return PlatformMessage(self.platform, str(chat_id), '')

    async def send_file(self, chat_id, path, *, caption='', filename=None):
        with path.open('rb') as handle:
            result = await self.bot.send_document(chat_id=chat_id, document=handle, filename=filename or path.name, caption=caption[:1000])
        return self._message(chat_id, result)

    async def send_animation(self, chat_id, path, *, caption=''):
        with path.open('rb') as handle:
            result = await self.bot.send_animation(chat_id=chat_id, animation=handle, caption=caption[:1000], read_timeout=120, write_timeout=120)
        return self._message(chat_id, result)

    async def answer_callback(self, event, *, text=None):
        if event.callback_id:
            await self.bot.answer_callback_query(callback_query_id=event.callback_id, text=text)


def _normalized(update):
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None:
        return None
    message = update.effective_message
    callback = update.callback_query
    text = message.text if message else None
    command = text.split()[0].lstrip('/').split('@')[0] if text and text.startswith('/') else None
    location = getattr(message, 'location', None)
    payload = callback.data if callback else None
    if payload == 'home:wn3':
        payload = encode_callback('product', 'open', 'weathernext3')
    elif payload and payload.startswith('wn3:'):
        parts = payload.split(':', 2)
        payload = encode_callback('wn3', parts[1], parts[2] if len(parts) > 2 else None)
    return NormalizedEvent('telegram', str(update.update_id), 'CALLBACK' if callback else 'LOCATION' if location else 'COMMAND' if command else 'TEXT',
        str(user.id), str(chat.id), message_id=str(message.message_id) if message else None,
        text=text, command=command, callback_payload=payload, callback_id=callback.id if callback else None,
        location=Location(location.latitude, location.longitude) if location else None)


async def wn3_update(update, context):
    event = _normalized(update)
    if event is None:
        return
    router = get_router()
    if event.callback_payload == encode_callback('wn3', 'home'):
        from telegram_personal_ux import _show_home
        await context.bot.answer_callback_query(callback_query_id=event.callback_id)
        router.sessions.clear(event.platform, event.user_id, event.chat_id)
        context.user_data.pop(SESSION_KEY, None)
        await _show_home(update.effective_message, int(event.user_id))
        raise ApplicationHandlerStop
    state = router.sessions.get(event.platform, event.user_id, event.chat_id)
    if event.event_type in {'TEXT', 'LOCATION'} and not context.user_data.get(SESSION_KEY):
        return
    if event.event_type in {'TEXT', 'LOCATION'} and state is None:
        return
    # Read the existing Telegram active point once; cloud and meteorological logic stay common.
    if event.command in {'wn3', 'weathernext3'} or event.callback_payload == encode_callback('product', 'open', 'weathernext3') or router.locations.active('telegram', event.user_id) is None:
        from telegram_user_state import get_active_location
        point = get_active_location(int(event.user_id))
        if point is not None:
            router.locations.remember('telegram', event.user_id, point, activate=True)
    context.user_data[SESSION_KEY] = {'active': True}
    await router.handle(event, TelegramGateway(context.bot))
    raise ApplicationHandlerStop


async def _command_guard(update, context):
    event = _normalized(update)
    if event is None:
        return
    router = get_router()
    active = context.user_data.get(SESSION_KEY) or router._job_key(event) in router.wn3_jobs
    if event.command in {'cancel', 'status'} and active:
        await router.handle(event, TelegramGateway(context.bot))
        if event.command == 'cancel':
            context.user_data.pop(SESSION_KEY, None)
        raise ApplicationHandlerStop
    if event.command not in {'wn3', 'weathernext3'}:
        router.sessions.clear('telegram', event.user_id, event.chat_id)
        context.user_data.pop(SESSION_KEY, None)


async def _navigation_guard(update, context):
    if update.callback_query and update.callback_query.data != 'home:wn3':
        event = _normalized(update)
        if event:
            get_router().sessions.clear('telegram', event.user_id, event.chat_id)
            context.user_data.pop(SESSION_KEY, None)


def _card_keyboard(params):
    # Compatibility rendering for already-issued legacy Telegram WN3 callbacks.
    markup = telegram_markup(common_keyboard(params))
    rows = []
    for row in markup.inline_keyboard:
        buttons = []
        for button in row:
            data = decode_callback(button.callback_data)
            payload = ':'.join(filter(lambda value: value is not None, ('wn3', data.action, data.value))) if data.scope == 'wn3' else button.callback_data
            buttons.append(InlineKeyboardButton(button.text, callback_data=payload))
        rows.append(buttons)
    return InlineKeyboardMarkup(rows)


def gateway_for_application(application):
    return TelegramGateway(application.bot) if application is not None else None
def _insert_wn3_button(keyboard: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = [list(row) for row in keyboard.inline_keyboard]
    if any(button.callback_data == "home:wn3" for row in rows for button in row):
        return keyboard
    insert_at = max(0, len(rows) - 1)
    rows.insert(insert_at, [InlineKeyboardButton("🛰 WeatherNext 3", callback_data="home:wn3")])
    return InlineKeyboardMarkup(rows)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    import telegram_concise_ux
    import telegram_personal_ux

    concise_keyboard = telegram_concise_ux.home_keyboard
    concise_text = telegram_concise_ux.home_text
    personal_keyboard = telegram_personal_ux.home_keyboard
    personal_text = telegram_personal_ux.home_text

    def patched_concise_keyboard():
        return _insert_wn3_button(concise_keyboard())

    def patched_concise_text():
        return concise_text().replace("🌦 GFS-прогнозы", "🌦 Модельные прогнозы GFS + WeatherNext 3")

    def patched_personal_keyboard(user_id: int):
        return _insert_wn3_button(personal_keyboard(user_id))

    def patched_personal_text(user_id: int):
        return personal_text(user_id).replace("🌦 GFS 0.25 · модельные прогнозы", "🌦 GFS 0.25 + WeatherNext 3 · модельные прогнозы")

    telegram_concise_ux.home_keyboard = patched_concise_keyboard
    telegram_concise_ux.home_text = patched_concise_text
    telegram_personal_ux.home_keyboard = patched_personal_keyboard
    telegram_personal_ux.home_text = patched_personal_text

    def add_manager_link(original, label, scope, action, value=None):
        def wrapped(*args, **kwargs):
            keyboard = original(*args, **kwargs)
            rows = [list(row) for row in keyboard.inline_keyboard]
            rows.append([InlineKeyboardButton(label, callback_data=encode_callback(scope, action, value))])
            return InlineKeyboardMarkup(rows)
        return wrapped
    telegram_personal_ux._settings_keyboard = add_manager_link(telegram_personal_ux._settings_keyboard,
        '🛰 Сценарии WeatherNext 3', 'settings', 'recipes', 0)
    import telegram_schedules
    telegram_schedules._manager_keyboard = add_manager_link(telegram_schedules._manager_keyboard,
        '🛰 Расписания WeatherNext 3', 'schedule', 'open')



def register(application):
    application.add_handler(CallbackQueryHandler(_navigation_guard, pattern=r'^(home:|recipe:)'), group=-13)
    application.add_handler(CommandHandler(['wn3', 'weathernext3'], wn3_update), group=-12)
    application.add_handler(CallbackQueryHandler(wn3_update, pattern=r'^(home:wn3|wn3:|w3\||v1\|(wn3|recipe|schedule|settings)\|)'), group=-12)
    application.add_handler(MessageHandler(filters.LOCATION | (filters.TEXT & ~filters.COMMAND), wn3_update), group=-12)
    application.add_handler(MessageHandler(filters.COMMAND, _command_guard), group=-11)
    old_shutdown = application.post_shutdown
    async def shutdown(app):
        if _ROUTER is not None:
            await _ROUTER.shutdown_wn3()
        if old_shutdown:
            await old_shutdown(app)
    application.post_shutdown = shutdown
