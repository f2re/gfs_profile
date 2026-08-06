from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import requests

from meteogram_models import (
    MeteogramError,
    MeteogramSeries,
    MeteogramSource,
    source_for_id,
    validate_days,
)
from meteogram_parse import (
    DETERMINISTIC_PARAMETERS,
    ENSEMBLE_PARAMETERS,
    _validate_payload_units,
    parse_deterministic_payload,
    parse_ensemble_payload,
)

CACHE_DIR = Path(os.getenv("GFS_CACHE_DIR", ".cache_gfs")) / "meteogram"
CACHE_TTL = int(os.getenv("METEOGRAM_CACHE_TTL", "10800"))
HTTP_TIMEOUT = float(os.getenv("METEOGRAM_HTTP_TIMEOUT", "90"))
HTTP_RETRIES = max(1, int(os.getenv("METEOGRAM_HTTP_RETRIES", "3")))

Progress = Callable[[str], None] | None


def fetch_meteogram(
    source_id: str,
    point_label: str,
    lat: float,
    lon: float,
    days: int,
    progress: Progress = None,
) -> MeteogramSeries:
    source = source_for_id(source_id)
    validate_days(source, days)
    params = {
        "latitude": round(float(lat), 5),
        "longitude": round(float(lon), 5),
        "hourly": ",".join(ENSEMBLE_PARAMETERS if source.ensemble else DETERMINISTIC_PARAMETERS),
        "models": source.upstream_id,
        "forecast_days": days,
        "timezone": "auto",
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
        "cell_selection": "nearest",
    }
    cache_path = _cache_path(source, params)
    payload = _read_cache(cache_path)
    from_cache = payload is not None
    if payload is None:
        if progress:
            progress("Загружаю модельные данные")
        payload = _request_json(source.endpoint, params)
        payload["_retrieved_at_utc"] = datetime.now(UTC).isoformat()
    elif progress:
        progress("Использую свежие данные из кэша")

    parser = parse_ensemble_payload if source.ensemble else parse_deterministic_payload

    def validate_and_parse(candidate: dict[str, Any]) -> MeteogramSeries:
        if progress:
            progress("Проверяю временной ряд и единицы")
        _validate_payload_units(candidate, source)
        return parser(
            candidate,
            source=source,
            point_label=point_label,
            requested_lat=lat,
            requested_lon=lon,
        )

    try:
        series = validate_and_parse(payload)
    except MeteogramError:
        if not from_cache:
            raise
        cache_path.unlink(missing_ok=True)
        if progress:
            progress("Кэш повреждён; повторно загружаю данные")
        payload = _request_json(source.endpoint, params)
        payload["_retrieved_at_utc"] = datetime.now(UTC).isoformat()
        series = validate_and_parse(payload)
        from_cache = False

    if not from_cache:
        _write_cache(cache_path, payload)
    return series


def _request_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=HTTP_TIMEOUT, headers={"User-Agent": "gfs-profile-telegram-bot/meteogram"})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("ожидался JSON-объект")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < HTTP_RETRIES:
                time.sleep(min(2 ** attempt, 4))
    raise MeteogramError(f"Источник данных недоступен: {last_error}")


def _cache_path(source: MeteogramSource, params: dict[str, Any]) -> Path:
    digest = hashlib.sha256(json.dumps({"source": source.source_id, "params": params}, sort_keys=True).encode()).hexdigest()[:24]
    return CACHE_DIR / f"{source.source_id}_{digest}.json"


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not (payload.get("hourly") or {}).get("time"):
            raise ValueError("повреждённый кэш")
        return payload
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
