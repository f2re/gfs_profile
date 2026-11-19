"""Shared product names and spacious home-menu layout for every messenger."""
from __future__ import annotations

from collections.abc import Iterable
import re

CANCEL_TEXT = "✖️ Отмена"
_CANCEL_TEXTS = frozenset(value.casefold() for value in (
    CANCEL_TEXT, "Отмена", "✖ Отмена", "✕ Отмена", "/cancel",
))


def is_cancel_text(text: str | None) -> bool:
    """Accept current and previously sent native reply-keyboard labels."""
    return (text or "").strip().casefold() in _CANCEL_TEXTS


HOME_TEXT = (
    "🌦️ GFS data\n\n"
    "Выберите продукт 👇\n"
    "Можно отправить город, координаты или геолокацию.\n"
    "Быстрый запрос: Москва +24."
)
PRODUCT_LABELS = {
    "profile": "Профиль",
    "aero": "Аэрограмма",
    "windgram": "Высота × срок",
    "cloudgram": "Облака",
    "map": "Карта",
    "meteogram": "Метеограмма",
    "icing": "Обледенение",
    "route": "Маршрут",
    "weathernext3": "WeatherNext 3",
}
PRODUCT_ICONS = {
    "profile": "📈",
    "aero": "🎈",
    "windgram": "🌬️",
    "cloudgram": "☁️",
    "map": "🗺️",
    "meteogram": "📊",
    "icing": "❄️",
    "route": "✈️",
    "weathernext3": "🌐",
}
# Two large controls per row keep all eight products visible on a phone.
PRODUCT_ROWS = (
    ("profile", "aero"),
    ("windgram", "cloudgram"),
    ("meteogram", "map"),
    ("icing", "route"),
    ("weathernext3",),
)


def product_button_label(product: str) -> str:
    label = PRODUCT_LABELS.get(product, product)
    icon = PRODUCT_ICONS.get(product)
    return f"{icon} {label}" if icon else label


def compact_point_label(label: str) -> str:
    """Short home-screen address; full location stays in settings and results."""
    value = str(label or "").strip()
    if not value:
        return "Точка"
    if re.fullmatch(r"(?:(?:геолокация|координаты|точка)\s+)?[+-]?\d+(?:\.\d+)?\s*,\s*[+-]?\d+(?:\.\d+)?", value, re.I):
        return value
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return value
    city = re.sub(r"^(?:г\.?|город|пгт\.?|пос[её]лок|п\.|с\.|село|д\.|деревня)\s+", "", parts[0], flags=re.I)
    region = next((part for part in parts[1:]
                   if re.search(r"\b(?:обл(?:асть)?\.?|край|республик\w*|автоном\w*)\b", part, re.I)
                   and part.casefold() != city.casefold()), None)
    return f"{city} · {region}" if region else city


def product_rows(enabled: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    available = frozenset(enabled)
    return tuple(filtered for row in PRODUCT_ROWS if (filtered := tuple(key for key in row if key in available)))
