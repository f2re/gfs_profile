"""Shared icing controls and aliases for native reply keyboards.

Inline controls are routed by callback action; only native reply controls need
text aliases. Keep their old labels accepted when a bot update changes styling.
"""
from __future__ import annotations

from .menu_catalog import CANCEL_TEXT

BUTTON_LABELS = {
    "water_level": "💧 Вода на уровне",
    "thickness": "↕ Толщина слоёв",
    "bounds": "↕ Границы слоёв",
    "maximum": "💧 Максимум по высоте",
    "water_column": "💧 Запас воды",
    "comparison": "⇄ Сравнение методов",
    "point": "📍 Пункт на карте",
    "help": "ℹ Как читать карту",
    "cancel": CANCEL_TEXT,
    "build": "▶️ Построить",
    "lead": "🕒 Срок",
    "level": "↕ Уровень",
    "contours": "≋ Изолинии",
    "products": "🗺 Другие карты",
    "refresh": "↻ Обновить данные",
    "png": "⬇ PNG целиком",
    "params": "⚙️ Параметры",
    "back": "⬅️ Назад",
    "back_card": "⬅️ К карте",
    "geolocation": "📍 Моя геолокация",
    "remove_point": "× Убрать пункт",
    "cancel_point": "↩ Отмена выбора пункта",
    "home": "🏠 Меню",
    "all_leads": "⋯ Все сроки до +384",
    "quick_leads": "⚡ Быстрые сроки",
    "retry": "↻ Проверить снова",
    "status": "⏳ Статус",
}

POINT_REPLY_LABELS = {
    "c": BUTTON_LABELS["back_card"],
    "d": BUTTON_LABELS["remove_point"],
}

_REPLY_ACTIONS = {
    "back": {"Назад", "Назад к карте", "Отмена выбора пункта",
             "← Назад", "← К карте",
             BUTTON_LABELS["back"], BUTTON_LABELS["back_card"], BUTTON_LABELS["cancel_point"]},
    "remove": {"Убрать пункт", BUTTON_LABELS["remove_point"]},
    "cancel": {"Отмена", "✖ Отмена", "✕ Отмена", BUTTON_LABELS["cancel"]},
    "home": {"Меню", "🏠 Главное меню", BUTTON_LABELS["home"]},
}


def point_reply_action(text: str | None) -> str | None:
    """Resolve current and previous native labels without stripping emoji."""
    value = (text or "").strip()
    return next((action for action, labels in _REPLY_ACTIONS.items() if value in labels), None)
