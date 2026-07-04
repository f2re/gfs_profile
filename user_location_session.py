from __future__ import annotations

import re
import time
from dataclasses import dataclass

from geocode import GeoPoint

RECENT_LOCATION_LIMIT = 4
RECENT_LOCATION_PREFIX = "🕘 "
RECENT_LOCATION_BUTTON_CHARS = 30
LEGACY_RECENT_LOCATION_INDEX_RE = re.compile(rf"^{re.escape(RECENT_LOCATION_PREFIX)}[1-9]\.\s*(?P<label>.+)$")
RECENT_LOCATIONS: dict[int, list["RecentLocation"]] = {}


@dataclass(frozen=True)
class RecentLocation:
    lat: float
    lon: float
    label: str
    source: str
    updated_at: float

    def to_point(self) -> GeoPoint:
        return GeoPoint(self.lat, self.lon, self.label, self.source)


def _clean_label(label: str | None, lat: float, lon: float) -> str:
    value = " ".join(str(label or "").split())
    return value if value else f"{lat:.4f}, {lon:.4f}"


def _is_duplicate(existing: RecentLocation, point: GeoPoint) -> bool:
    close = abs(existing.lat - point.lat) <= 0.01 and abs(existing.lon - point.lon) <= 0.01
    same_label = existing.label.casefold() == _clean_label(point.label, point.lat, point.lon).casefold()
    return close or (same_label and abs(existing.lat - point.lat) <= 0.05 and abs(existing.lon - point.lon) <= 0.05)


def _truncate_label(label: str, max_chars: int) -> str:
    if len(label) <= max_chars:
        return label
    return label[: max(1, max_chars - 1)] + "…"


def remember_location(user_id: int, point: GeoPoint) -> None:
    if user_id <= 0:
        return
    label = _clean_label(point.label, point.lat, point.lon)
    entry = RecentLocation(float(point.lat), float(point.lon), label, str(point.source or "manual"), time.time())
    current = RECENT_LOCATIONS.get(user_id, [])
    kept = [item for item in current if not _is_duplicate(item, point)]
    RECENT_LOCATIONS[user_id] = [entry, *kept][:RECENT_LOCATION_LIMIT]


def get_recent_locations(user_id: int, limit: int = RECENT_LOCATION_LIMIT) -> list[GeoPoint]:
    if user_id <= 0:
        return []
    return [entry.to_point() for entry in RECENT_LOCATIONS.get(user_id, [])[: max(0, int(limit))]]


def clear_recent_locations(user_id: int) -> None:
    RECENT_LOCATIONS.pop(user_id, None)


def recent_location_button_label(point: GeoPoint, max_chars: int = RECENT_LOCATION_BUTTON_CHARS, index: int | None = None) -> str:
    del index
    label = _clean_label(point.label, point.lat, point.lon)
    if label.lower() in {"точка", "геолокация telegram", "telegram location"}:
        label = f"{point.lat:.4f}, {point.lon:.4f}"
    return RECENT_LOCATION_PREFIX + _truncate_label(label, max_chars)


def _candidate_button_texts(value: str) -> list[str]:
    values = [value]
    legacy_match = LEGACY_RECENT_LOCATION_INDEX_RE.match(value)
    if legacy_match:
        values.append(RECENT_LOCATION_PREFIX + legacy_match.group("label"))
    return values


def match_recent_location_button(user_id: int, text: str) -> GeoPoint | None:
    value = str(text or "").strip()
    if not value.startswith(RECENT_LOCATION_PREFIX):
        return None
    candidates = _candidate_button_texts(value)
    for point in get_recent_locations(user_id):
        labels = {
            recent_location_button_label(point),
            recent_location_button_label(point, max_chars=28),
            recent_location_button_label(point, max_chars=RECENT_LOCATION_BUTTON_CHARS),
        }
        if any(label in candidates for label in labels):
            return point
    return None
