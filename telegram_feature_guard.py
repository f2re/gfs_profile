"""Reject disabled sources without importing their Telegram adapter or provider."""
from __future__ import annotations

from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, MessageHandler, filters
from feature_flags import DISABLED_SOURCE_MESSAGE, disabled_input, product_available, weathernext3_enabled


async def guard(update, context) -> None:
    if weathernext3_enabled():
        return
    message = update.effective_message
    query = update.callback_query
    blocked = disabled_input(getattr(message, 'text', '') or '', getattr(query, 'data', '') or '')
    # A previous in-memory wizard must never continue into a disabled provider.
    stale = False
    for key in ('weathernext3_wizard', 'meteogram_wizard', 'schedule_wizard'):
        state = context.user_data.get(key)
        if not isinstance(state, dict):
            continue
        spec = state.get('spec', state)
        product = spec.get('product', 'weathernext3' if key == 'weathernext3_wizard' else 'meteogram')
        if not product_available(product, spec.get('params', spec)):
            context.user_data.pop(key, None)
            stale = True
    # Let /start, /cancel and other commands recover normally from stale state.
    text = getattr(message, 'text', '') or ''
    if not blocked and not (stale and (query is not None or not text.startswith('/'))):
        return
    if query is not None:
        await query.answer(DISABLED_SOURCE_MESSAGE)
    elif message is not None:
        await message.reply_text(DISABLED_SOURCE_MESSAGE)
    raise ApplicationHandlerStop


def register(application) -> None:
    # Negative group wins before old source/recipe handlers. No worker is started.
    application.add_handler(CallbackQueryHandler(guard), group=-100)
    application.add_handler(MessageHandler(filters.ALL, guard), group=-100)
