from __future__ import annotations

"""Compatibility facade for persistent Telegram locations."""

import re
from dataclasses import dataclass

from geocode import GeoPoint
from telegram_user_state import (
    clear_locations as _clear_locations,
    get_active_location as _get_active_location,
    get_recent_locations as _get_recent_locations,
    remember_location as _remember_location,
)

RECENT_LOCATION_LIMIT = 4
RECENT_LOCATION_PREFIX = "🕘 "
RECENT_LOCATION_BUTTON_CHARS = 30
LEGACY_RECENT_LOCATION_INDEX_RE = re.compile(
    rf"^{re.escape(RECENT_LOCATION_PREFIX)}[1-9]\.\s*(?P<label>.+)$"
)

# Retained for import compatibility. SQLite is the source of truth.
RECENT_LOCATIONS: dict[int, list["RecentLocation"]] = {}


@dataclass(frozen=True)
class RecentLocation:
    lat: float
    lon: float
    label: str
    source: str
    updated_at: float = 0.0

    def to_point(self) -> GeoPoint:
        return GeoPoint(self.lat, self.lon, self.label, self.source)


def _clean_label(label: str | None, lat: float, lon: float) -> str:
    value = " ".join(str(label or "").split())
    return value if value else f"{lat:.4f}, {lon:.4f}"


def _truncate_label(label: str, max_chars: int) -> str:
    if len(label) <= max_chars:
        return label
    return label[: max(1, max_chars - 1)] + "…"


def remember_location(user_id: int, point: GeoPoint, *, activate: bool = True) -> None:
    """Persist a location and, by default, make it the active point."""

    if int(user_id) <= 0:
        return
    _remember_location(int(user_id), point, activate=activate)


def remember_location_without_activation(user_id: int, point: GeoPoint) -> None:
    """Remember a route endpoint without changing the point used by products."""

    remember_location(user_id, point, activate=False)


def get_recent_locations(
    user_id: int,
    limit: int = RECENT_LOCATION_LIMIT,
) -> list[GeoPoint]:
    if int(user_id) <= 0:
        return []
    return [
        GeoPoint(item.lat, item.lon, item.label, item.source)
        for item in _get_recent_locations(int(user_id), max(0, int(limit)))
    ]


def get_active_location(user_id: int) -> GeoPoint | None:
    if int(user_id) <= 0:
        return None
    item = _get_active_location(int(user_id))
    if item is None:
        return None
    return GeoPoint(item.lat, item.lon, item.label, item.source)


def clear_recent_locations(user_id: int) -> None:
    RECENT_LOCATIONS.pop(int(user_id), None)
    _clear_locations(int(user_id))


def recent_location_button_label(
    point: GeoPoint,
    max_chars: int = RECENT_LOCATION_BUTTON_CHARS,
    index: int | None = None,
) -> str:
    del index
    label = _clean_label(point.label, point.lat, point.lon)
    if label.lower() in {
        "точка",
        "геолокация telegram",
        "telegram location",
        "текущая геолокация",
        "последняя геолокация",
    }:
        label = f"{point.lat:.4f}, {point.lon:.4f}"
    return RECENT_LOCATION_PREFIX + _truncate_label(label, max_chars)


def _candidate_button_texts(value: str) -> list[str]:
    values = [value]
    legacy_match = LEGACY_RECENT_LOCATION_INDEX_RE.match(value)
    if legacy_match:
        values.append(RECENT_LOCATION_PREFIX + legacy_match.group("label"))
    return values


def _received_label_width(value: str) -> int:
    return max(1, len(value) - len(RECENT_LOCATION_PREFIX))


def match_recent_location_button(user_id: int, text: str) -> GeoPoint | None:
    value = str(text or "").strip()
    if not value.startswith(RECENT_LOCATION_PREFIX):
        return None
    candidates = _candidate_button_texts(value)
    for point in get_recent_locations(user_id):
        labels = {
            recent_location_button_label(point),
            recent_location_button_label(point, max_chars=28),
        }
        labels.update(
            recent_location_button_label(
                point,
                max_chars=_received_label_width(candidate),
            )
            for candidate in candidates
        )
        if any(label in candidates for label in labels):
            return point
    return None
