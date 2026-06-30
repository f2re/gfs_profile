from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

LEAD_BUTTONS = (0, 3, 6, 12, 24, 48)
LEAD_LABELS = {
    0: "+0 ч анализ",
    3: "+3 ч",
    6: "+6 ч",
    12: "+12 ч",
    24: "+24 ч сутки",
    48: "+48 ч 2 суток",
}


def lead_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(LEAD_LABELS.get(lead, f"+{lead} ч"), callback_data=f"lead:{lead}") for lead in LEAD_BUTTONS]
    return InlineKeyboardMarkup([buttons[:3], buttons[3:]])


def place_keyboard(labels: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for index, label in enumerate(labels[:3]):
        if len(label) > 54:
            label = label[:51] + "…"
        rows.append([InlineKeyboardButton(f"{index + 1}. {label}", callback_data=f"place:{index}")])
    rows.append([InlineKeyboardButton("✖️ Отменить", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Отправить геолокацию", request_location=True)], [KeyboardButton("❓ Помощь")]],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Город, координаты или /profile Москва +24",
    )
