"""Shared product names and spacious home-menu layout for every messenger."""
from __future__ import annotations

from collections.abc import Iterable

HOME_TEXT = (
    "GFS data\n\n"
    "Выберите продукт или отправьте город, координаты, геолокацию.\n"
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
# Longer names get a full-width button, including in clients that truncate labels.
PRODUCT_ROWS = (
    ("profile", "aero"),
    ("windgram",),
    ("cloudgram", "map"),
    ("meteogram",),
    ("icing",),
    ("route",),
    ("weathernext3",),
)


def product_rows(enabled: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    available = frozenset(enabled)
    return tuple(filtered for row in PRODUCT_ROWS if (filtered := tuple(key for key in row if key in available)))
