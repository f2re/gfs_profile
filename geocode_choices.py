from __future__ import annotations

import os

import requests

from geocode import GeoPoint, GeocodeError, local_lookup, parse_coordinates, read_cached, write_cached

NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
NOMINATIM_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "gfs-profile-telegram-bot/0.1")
GEOCODE_TIMEOUT = int(os.getenv("GEOCODE_TIMEOUT", "12"))


def search_location_candidates(query: str, limit: int = 3) -> list[GeoPoint]:
    point = parse_coordinates(query)
    if point:
        return [point]

    local = local_lookup(query)
    if local:
        return [local]

    cached = read_cached(query)
    if cached:
        return [cached]

    headers = {"User-Agent": NOMINATIM_USER_AGENT}
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": max(1, min(limit, 5)),
        "addressdetails": 0,
        "accept-language": "ru,en",
    }
    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=GEOCODE_TIMEOUT)
        response.raise_for_status()
        items = response.json()
    except Exception as exc:
        raise GeocodeError(f"Ошибка геокодирования: {exc}") from exc

    points = [
        GeoPoint(
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            label=str(item.get("display_name") or query),
            source="nominatim",
        )
        for item in items
    ]
    if len(points) == 1:
        write_cached(query, points[0])
    return points
