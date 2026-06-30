from __future__ import annotations

import math

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from gfs_core import canonical_leads

COMMON_LEADS = (0, 3, 6, 12, 24, 48)
LEAD_PAGE_SIZE = 15
MAX_LEAD = 384
LEAD_LABELS = {
    0: "+0 ч анализ",
    3: "+3 ч",
    6: "+6 ч",
    12: "+12 ч",
    24: "+24 ч сутки",
    48: "+48 ч 2 суток",
}


def _lead_label(lead: int) -> str:
    if lead in LEAD_LABELS:
        return LEAD_LABELS[lead]
    if lead % 24 == 0:
        return f"+{lead} ч ({lead // 24} сут)"
    return f"+{lead} ч"


def lead_page_count() -> int:
    return math.ceil(len(canonical_leads()) / LEAD_PAGE_SIZE)


def lead_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    """Build lead selector. Page 0 is compact/common; pages 1..N expose all GFS leads to +384 h."""

    if page <= 0:
        buttons = [InlineKeyboardButton(_lead_label(lead), callback_data=f"lead:{lead}") for lead in COMMON_LEADS]
        return InlineKeyboardMarkup(
            [
                buttons[:3],
                buttons[3:],
                [InlineKeyboardButton("Все сроки до +384 ч →", callback_data="leadpage:1")],
                [InlineKeyboardButton("Макс. +384 ч", callback_data="lead:384")],
            ]
        )

    leads = canonical_leads()
    total_pages = lead_page_count()
    page = max(1, min(page, total_pages))
    start = (page - 1) * LEAD_PAGE_SIZE
    chunk = leads[start : start + LEAD_PAGE_SIZE]
    rows = []
    for i in range(0, len(chunk), 3):
        rows.append([InlineKeyboardButton(_lead_label(lead), callback_data=f"lead:{lead}") for lead in chunk[i : i + 3]])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("←", callback_data=f"leadpage:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("→", callback_data=f"leadpage:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("Популярные сроки", callback_data="leadpage:0"), InlineKeyboardButton("Макс. +384 ч", callback_data="lead:384")])
    return InlineKeyboardMarkup(rows)


def lead_page_text(page: int = 0) -> str:
    if page <= 0:
        return "Выберите срок прогноза. Сначала показаны самые частые сроки; полный диапазон GFS — до +384 ч."
    return f"Выберите срок прогноза: страница {max(1, min(page, lead_page_count()))}/{lead_page_count()}, доступен полный диапазон до +384 ч."


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
