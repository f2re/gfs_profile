from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from geocode import GeoPoint, GeocodeError

DADATA_SUGGEST_URL = os.getenv(
    "DADATA_SUGGEST_URL",
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
)
DADATA_TIMEOUT = int(os.getenv("DADATA_TIMEOUT", os.getenv("GEOCODE_TIMEOUT", "12")))


@dataclass(frozen=True)
class DadataSuggestion:
    point: GeoPoint
    fias_id: str | None
    object_type: str | None
    region: str | None


def dadata_api_key() -> str:
    return os.getenv("DADATA_API_KEY", "").strip()


def _candidate_label(value: str, data: dict[str, object]) -> str:
    settlement = str(data.get("settlement_with_type") or "").strip()
    city = str(data.get("city_with_type") or "").strip()
    region = str(data.get("region_with_type") or "").strip()
    area = str(data.get("area_with_type") or "").strip()
    primary = settlement or city or region or value.strip()
    parts = [primary]
    for extra in (area, region):
        if extra and extra not in parts and extra not in primary:
            parts.append(extra)
    return ", ".join(parts)


def _object_type(data: dict[str, object]) -> str | None:
    if data.get("settlement"):
        return str(data.get("settlement_type_full") or "населённый пункт")
    if data.get("city"):
        return str(data.get("city_type_full") or "город")
    if data.get("region"):
        return str(data.get("region_type_full") or "регион")
    return None


def search_dadata(query: str, limit: int = 5) -> list[GeoPoint]:
    token = dadata_api_key()
    if not token:
        raise GeocodeError("DADATA_API_KEY не задан")

    count = max(1, min(int(limit), 20))
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "query": query,
        "count": count,
        "language": "ru",
        "division": "administrative",
    }
    try:
        response = requests.post(DADATA_SUGGEST_URL, json=payload, headers=headers, timeout=DADATA_TIMEOUT)
    except requests.RequestException as exc:
        raise GeocodeError(f"DaData недоступна: {exc}") from exc

    if response.status_code == 401:
        raise GeocodeError("DaData отклонила запрос: отсутствует API-ключ")
    if response.status_code == 403:
        raise GeocodeError("DaData отклонила API-ключ, почта не подтверждена или исчерпан дневной лимит")
    if response.status_code == 429:
        raise GeocodeError("DaData временно ограничила частоту запросов")
    try:
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodeError(f"Некорректный ответ DaData: {exc}") from exc

    suggestions = body.get("suggestions") if isinstance(body, dict) else None
    if not isinstance(suggestions, list):
        raise GeocodeError("DaData вернула ответ без массива suggestions")

    points: list[GeoPoint] = []
    seen: set[tuple[float, float, str]] = set()
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        lat_raw = data.get("geo_lat")
        lon_raw = data.get("geo_lon")
        if lat_raw in (None, "") or lon_raw in (None, ""):
            continue
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
        except (TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        value = str(item.get("value") or item.get("unrestricted_value") or query).strip()
        label = _candidate_label(value, data)
        key = (round(lat, 6), round(lon, 6), label.casefold())
        if key in seen:
            continue
        seen.add(key)
        points.append(GeoPoint(lat=lat, lon=lon, label=label, source="dadata"))
        if len(points) >= count:
            break
    return points


def validate_dadata_access() -> GeoPoint:
    points = search_dadata("Москва", 1)
    if not points:
        raise GeocodeError("DaData не вернула координаты для контрольного запроса «Москва»")
    return points[0]
