from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from requests import RequestException

GEOCODE_TIMEOUT = int(os.getenv("GEOCODE_TIMEOUT", "12"))
GEOCODE_CACHE_TTL_SECONDS = int(os.getenv("GEOCODE_CACHE_TTL_SECONDS", str(30 * 24 * 3600)))
GEOCODE_CACHE_DIR = Path(os.getenv("GEOCODE_CACHE_DIR", ".cache_gfs/geocode"))
GEOCODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
NOMINATIM_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "gfs-profile-telegram-bot/0.1")


class GeocodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    label: str
    source: str = "manual"


LOCAL_POINTS: dict[str, GeoPoint] = {
    "moskva": GeoPoint(55.7558, 37.6173, "Москва", "local"),
    "москва": GeoPoint(55.7558, 37.6173, "Москва", "local"),
    "saint petersburg": GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "local"),
    "st petersburg": GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "local"),
    "spb": GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "local"),
    "спб": GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "local"),
    "санкт петербург": GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "local"),
    "санкт-петербург": GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "local"),
    "krasnodar": GeoPoint(45.0355, 38.9753, "Краснодар", "local"),
    "краснодар": GeoPoint(45.0355, 38.9753, "Краснодар", "local"),
    "pyatigorsk": GeoPoint(44.0393, 43.0708, "Пятигорск", "local"),
    "пятигорск": GeoPoint(44.0393, 43.0708, "Пятигорск", "local"),
    "mineralnye vody": GeoPoint(44.2103, 43.1353, "Минеральные Воды", "local"),
    "минеральные воды": GeoPoint(44.2103, 43.1353, "Минеральные Воды", "local"),
    "london": GeoPoint(51.5074, -0.1278, "London", "local"),
    "лондон": GeoPoint(51.5074, -0.1278, "London", "local"),
}

_COORD_RE = re.compile(
    r"^\s*(?P<lat>[+-]?\d+(?:[\.,]\d+)?)\s*[,;\s]+(?P<lon>[+-]?\d+(?:[\.,]\d+)?)\s*$"
)


def normalize_query(query: str) -> str:
    text = unicodedata.normalize("NFKC", query.strip().lower())
    text = re.sub(r"\s+", " ", text)
    return text


def parse_coordinates(text: str) -> GeoPoint | None:
    match = _COORD_RE.match(text.strip())
    if not match:
        return None
    lat = float(match.group("lat").replace(",", "."))
    lon = float(match.group("lon").replace(",", "."))
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise GeocodeError("Координаты вне допустимого диапазона")
    return GeoPoint(lat=lat, lon=lon, label=f"{lat:.4f}, {lon:.4f}", source="coordinates")


def cache_path(query: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ._-]+", "_", normalize_query(query)).strip("_") or "query"
    return GEOCODE_CACHE_DIR / f"{safe[:80]}.json"


def read_cached(query: str) -> GeoPoint | None:
    path = cache_path(query)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(payload.get("created_at", 0)) > GEOCODE_CACHE_TTL_SECONDS:
            return None
        return GeoPoint(
            lat=float(payload["lat"]),
            lon=float(payload["lon"]),
            label=str(payload["label"]),
            source=str(payload.get("source", "cache")),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def write_cached(query: str, point: GeoPoint) -> None:
    payload = {
        "lat": point.lat,
        "lon": point.lon,
        "label": point.label,
        "source": point.source,
        "created_at": time.time(),
    }
    try:
        cache_path(query).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def local_lookup(query: str) -> GeoPoint | None:
    return LOCAL_POINTS.get(normalize_query(query))


def nominatim_lookup(query: str) -> GeoPoint:
    headers = {"User-Agent": NOMINATIM_USER_AGENT}
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 0,
        "accept-language": "ru,en",
    }
    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=GEOCODE_TIMEOUT)
        response.raise_for_status()
        items = response.json()
    except (RequestException, ValueError) as exc:
        raise GeocodeError(f"Ошибка геокодирования: {exc}") from exc

    if not items:
        raise GeocodeError("Город или место не найдено. Пришлите координаты или геолокацию Telegram.")

    item = items[0]
    return GeoPoint(
        lat=float(item["lat"]),
        lon=float(item["lon"]),
        label=str(item.get("display_name") or query),
        source="nominatim",
    )


def resolve_location(query: str) -> GeoPoint:
    point = parse_coordinates(query)
    if point:
        return point

    local = local_lookup(query)
    if local:
        return local

    cached = read_cached(query)
    if cached:
        return cached

    point = nominatim_lookup(query)
    write_cached(query, point)
    return point


def known_locations() -> Iterable[str]:
    seen: set[str] = set()
    for point in LOCAL_POINTS.values():
        if point.label not in seen:
            seen.add(point.label)
            yield point.label
