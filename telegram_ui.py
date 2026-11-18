from __future__ import annotations

import math

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from geocode import GeoPoint
from gfs_core import canonical_leads
from messenger.menu_catalog import PRODUCT_LABELS, product_rows
from user_location_session import recent_location_button_label

CANCEL_TEXT = "✖ Отмена"
COMMON_LEADS = (0, 3, 6, 12, 24, 48)
LEAD_PAGE_SIZE = 12
MAX_LEAD = 384


def _lead_label(lead: int) -> str:
    return f"+{lead} ч"


def button_rows(buttons, columns: int = 2) -> list[list[InlineKeyboardButton]]:
    """Keep action labels readable; callers may use three short numeric options."""
    buttons = list(buttons)
    return [buttons[index:index + columns] for index in range(0, len(buttons), columns)]


def product_button_rows(prefix: str = "home", products=None) -> list[list[InlineKeyboardButton]]:
    enabled = products if products is not None else set(PRODUCT_LABELS) - {"weathernext3"}
    return [
        [InlineKeyboardButton(PRODUCT_LABELS[product], callback_data=f"{prefix}:{product}") for product in row]
        for row in product_rows(enabled)
    ]


def native_callback_payload(payload: str | None) -> str | None:
    """Return from a shared product card to the complete native Telegram menu."""
    return {"v1|flow|home": "prefs:home", "v1|flow|help": "home:help"}.get(payload, payload)


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
                [InlineKeyboardButton("Все сроки →", callback_data="leadpage:1")],
                [InlineKeyboardButton("+384 ч", callback_data="lead:384"), InlineKeyboardButton("Отмена", callback_data="cancel")],
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
    rows.append([InlineKeyboardButton("Популярные", callback_data="leadpage:0"), InlineKeyboardButton("+384 ч", callback_data="lead:384")])
    rows.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def lead_page_text(page: int = 0) -> str:
    if page <= 0:
        return "Выберите срок."
    return f"Все сроки: страница {max(1, min(page, lead_page_count()))}/{lead_page_count()}."


def place_keyboard(labels: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for index, label in enumerate(labels[:3]):
        if len(label) > 54:
            label = label[:51] + "…"
        rows.append([InlineKeyboardButton(label, callback_data=f"place:{index}")])
    rows.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def location_keyboard(recent_locations: list[GeoPoint] | None = None) -> ReplyKeyboardMarkup:
    """Temporary keyboard shown only while a point is requested."""

    rows: list[list[KeyboardButton]] = [[KeyboardButton("📍 Моя геолокация", request_location=True)]]
    recent_buttons = [KeyboardButton(recent_location_button_label(point)) for point in (recent_locations or [])[:4]]
    for index in range(0, len(recent_buttons), 2):
        rows.append(recent_buttons[index : index + 2])
    rows.append([KeyboardButton(CANCEL_TEXT)])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
        is_persistent=False,
        input_field_placeholder="Город или координаты",
    )
