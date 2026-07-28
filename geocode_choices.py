from __future__ import annotations

import os

import requests

from dadata_geocoder import search_dadata
from geocode import GeoPoint, GeocodeError, local_lookup, parse_coordinates, read_cached, write_cached

NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
NOMINATIM_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "gfs-profile-telegram-bot/0.1")
GEOCODE_TIMEOUT = int(os.getenv("GEOCODE_TIMEOUT", "12"))
DEFAULT_GEOCODER_PROVIDERS = ("dadata", "local", "nominatim")
_ALLOWED_PROVIDERS = frozenset(DEFAULT_GEOCODER_PROVIDERS)


def configured_geocoder_providers() -> tuple[str, ...]:
    raw = os.getenv("GEOCODER_PROVIDERS", ",".join(DEFAULT_GEOCODER_PROVIDERS))
    providers: list[str] = []
    for item in raw.split(","):
        provider = item.strip().lower()
        if not provider:
            continue
        if provider not in _ALLOWED_PROVIDERS:
            raise GeocodeError(f"Неизвестный геокодер: {provider}")
        if provider not in providers:
            providers.append(provider)
    if not providers:
        raise GeocodeError("GEOCODER_PROVIDERS не содержит провайдеров")
    return tuple(providers)


def _search_nominatim(query: str, limit: int) -> list[GeoPoint]:
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
        raise GeocodeError(f"Ошибка Nominatim: {exc}") from exc
    if not isinstance(items, list):
        raise GeocodeError("Nominatim вернул некорректный ответ")
    return [
        GeoPoint(
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            label=str(item.get("display_name") or query),
            source="nominatim",
        )
        for item in items
        if isinstance(item, dict) and "lat" in item and "lon" in item
    ]


def _cache_matches_primary(point: GeoPoint, providers: tuple[str, ...]) -> bool:
    return bool(providers and point.source == providers[0])


def _may_cache_result(provider: str, requested_limit: int, points: list[GeoPoint]) -> bool:
    if len(points) != 1:
        return False
    # DaData Suggestions is an interactive suggestion service. A count=1 result
    # was not confirmed by a person and may hide ambiguity, so do not persist it.
    if provider == "dadata" and int(requested_limit) <= 1:
        return False
    return True


def search_location_candidates(query: str, limit: int = 3) -> list[GeoPoint]:
    point = parse_coordinates(query)
    if point:
        return [point]

    providers = configured_geocoder_providers()
    cached = read_cached(query)
    if cached and _cache_matches_primary(cached, providers):
        return [cached]

    errors: list[str] = []
    for provider in providers:
        try:
            if provider == "dadata":
                points = search_dadata(query, limit)
            elif provider == "local":
                local = local_lookup(query)
                points = [local] if local else []
            elif provider == "nominatim":
                points = _search_nominatim(query, limit)
            else:
                continue
        except GeocodeError as exc:
            errors.append(f"{provider}: {exc}")
            continue

        if points:
            if _may_cache_result(provider, limit, points):
                write_cached(query, points[0])
            return points[: max(1, int(limit))]

    details = "; ".join(errors)
    if details:
        raise GeocodeError(f"Место не найдено. Провайдеры: {details}")
    raise GeocodeError("Место не найдено")
