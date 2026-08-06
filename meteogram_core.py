from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import requests

CACHE_DIR = Path(os.getenv("GFS_CACHE_DIR", ".cache_gfs")) / "meteogram"
CACHE_TTL = int(os.getenv("METEOGRAM_CACHE_TTL", "10800"))
HTTP_TIMEOUT = float(os.getenv("METEOGRAM_HTTP_TIMEOUT", "90"))
HTTP_RETRIES = max(1, int(os.getenv("METEOGRAM_HTTP_RETRIES", "3")))

DETERMINISTIC_PARAMETERS = (
    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "precipitation", "weather_code", "pressure_msl", "cloud_cover",
    "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "is_day",
)
ENSEMBLE_PARAMETERS = (
    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "precipitation", "weather_code", "pressure_msl", "cloud_cover",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
)
MEMBER_RE = re.compile(r"^(?P<name>.+)_member(?P<member>\d+)$")


class MeteogramError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MeteogramSource:
    source_id: str
    label: str
    model: str
    provider: str
    endpoint: str
    upstream_id: str
    horizon_days: int
    ensemble: bool = False
    expected_members: int | None = None
    resolution: str | None = None


SOURCES = (
    MeteogramSource("gfs", "GFS · NOAA", "NOAA GFS 0.25°", "NOAA/NCEP через Open-Meteo", "https://api.open-meteo.com/v1/gfs", "gfs025", 16, resolution="0.25°"),
    MeteogramSource("ecmwf_ifs", "ECMWF IFS", "ECMWF IFS 0.25° Open Data", "ECMWF через Open-Meteo", "https://api.open-meteo.com/v1/ecmwf", "ecmwf_ifs025", 15, resolution="0.25°"),
    MeteogramSource("ecmwf_aifs", "ECMWF AIFS", "ECMWF AIFS 0.25° Single", "ECMWF через Open-Meteo", "https://api.open-meteo.com/v1/ecmwf", "ecmwf_aifs025_single", 15, resolution="0.25°"),
    MeteogramSource("icon_global", "ICON · DWD", "DWD ICON Global", "DWD через Open-Meteo", "https://api.open-meteo.com/v1/dwd-icon", "dwd_icon_global", 8, resolution="около 11 км"),
    MeteogramSource("gem_gdps", "GEM · ECCC", "ECCC GEM Global (GDPS)", "ECCC через Open-Meteo", "https://api.open-meteo.com/v1/gem", "cmc_gem_gdps", 10, resolution="около 15 км"),
    MeteogramSource("gefs", "GEFS · NOAA", "NOAA GEFS 0.25°", "NOAA/NCEP через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "ncep_gefs025", 10, True, 31, "0.25°"),
    MeteogramSource("ecmwf_ens", "ECMWF ENS", "ECMWF IFS Ensemble 0.25°", "ECMWF через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "ecmwf_ifs025_ensemble", 15, True, 51, "0.25°"),
    MeteogramSource("aifs_ens", "AIFS ENS", "ECMWF AIFS Ensemble 0.25°", "ECMWF через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "ecmwf_aifs025_ensemble", 15, True, 51, "0.25°"),
    MeteogramSource("icon_eps", "ICON-EPS", "DWD ICON Global EPS", "DWD через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "icon_global_eps", 8, True, 40),
    MeteogramSource("geps", "GEPS · ECCC", "ECCC Global Ensemble Prediction System", "ECCC через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "gem_global_ensemble", 16, True, 21),
)
SOURCE_BY_ID = {source.source_id: source for source in SOURCES}
ALIASES = {
    "noaa": "gfs", "gfs025": "gfs", "ecmwf": "ecmwf_ifs", "ifs": "ecmwf_ifs",
    "aifs": "ecmwf_aifs", "icon": "icon_global", "gem": "gem_gdps",
    "ens": "ecmwf_ens", "ecmwf_ensemble": "ecmwf_ens", "gefs025": "gefs",
    "aifs_ensemble": "aifs_ens", "icon-eps": "icon_eps", "gem_ensemble": "geps",
}


@dataclass(slots=True)
class MeteogramSeries:
    source: MeteogramSource
    point_label: str
    requested_lat: float
    requested_lon: float
    grid_lat: float | None
    grid_lon: float | None
    timezone: str
    times: list[datetime]
    fields: dict[str, np.ndarray]
    stats: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    retrieved_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    member_count: int | None = None
    expected_member_count: int | None = None
    warnings: list[str] = field(default_factory=list)

    def values(self, name: str) -> np.ndarray:
        return self.fields.get(name, np.full(len(self.times), np.nan, dtype=float))

    def statistic(self, name: str, stat: str) -> np.ndarray:
        return self.stats.get(name, {}).get(stat, np.full(len(self.times), np.nan, dtype=float))


Progress = Callable[[str], None] | None


def source_for_id(value: str) -> MeteogramSource:
    key = str(value or "gfs").strip().lower().replace("-", "_")
    key = ALIASES.get(key, key)
    try:
        return SOURCE_BY_ID[key]
    except KeyError as exc:
        raise MeteogramError(f"Неизвестная модель: {value}") from exc


def sources_by_kind(ensemble: bool) -> tuple[MeteogramSource, ...]:
    return tuple(source for source in SOURCES if source.ensemble is ensemble)


def available_periods(source: MeteogramSource) -> tuple[int, ...]:
    values = tuple(value for value in (3, 5, 8, 10, 15, 16) if value <= source.horizon_days)
    return values or (source.horizon_days,)


def validate_days(source: MeteogramSource, days: int) -> int:
    if days < 1 or days > source.horizon_days:
        raise MeteogramError(f"Для {source.label} доступно 1–{source.horizon_days} суток")
    return days


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
    if payload is None:
        if progress:
            progress("Загружаю модельные данные")
        payload = _request_json(source.endpoint, params)
        payload["_retrieved_at_utc"] = datetime.now(UTC).isoformat()
        _write_cache(cache_path, payload)
    elif progress:
        progress("Использую свежие данные из кэша")
    if progress:
        progress("Проверяю временной ряд и единицы")
    parser = parse_ensemble_payload if source.ensemble else parse_deterministic_payload
    return parser(payload, source=source, point_label=point_label, requested_lat=lat, requested_lon=lon)


def parse_deterministic_payload(
    payload: dict[str, Any], *, source: MeteogramSource, point_label: str,
    requested_lat: float, requested_lon: float,
) -> MeteogramSeries:
    meta = _metadata(payload, source, point_label, requested_lat, requested_lon)
    hourly = payload.get("hourly") or {}
    count = len(meta["times"])
    fields = {name: _numeric(hourly.get(name), count) for name in DETERMINISTIC_PARAMETERS}
    fields["precipitation_intensity"] = _precip_intensity(fields["precipitation"], meta["times"])
    return MeteogramSeries(fields=fields, **meta)


def parse_ensemble_payload(
    payload: dict[str, Any], *, source: MeteogramSource, point_label: str,
    requested_lat: float, requested_lon: float,
) -> MeteogramSeries:
    meta = _metadata(payload, source, point_label, requested_lat, requested_lon)
    hourly = payload.get("hourly") or {}
    count = len(meta["times"])
    members: dict[str, dict[int, np.ndarray]] = {}
    for key, values in hourly.items():
        match = MEMBER_RE.match(str(key))
        if match:
            members.setdefault(match.group("name"), {})[int(match.group("member"))] = _numeric(values, count)
    if not members:
        raise MeteogramError("Ансамблевый ответ не содержит отдельных членов")
    all_ids = {member for group in members.values() for member in group}
    fields: dict[str, np.ndarray] = {}
    stats: dict[str, dict[str, np.ndarray]] = {}
    for name in ENSEMBLE_PARAMETERS:
        matrix = _matrix(members.get(name, {}), count)
        if matrix.size == 0:
            fields[name] = np.full(count, np.nan)
            continue
        if name == "wind_direction_10m":
            fields[name], fields["wind_direction_resultant"] = _circular_mean(matrix)
            continue
        if name == "weather_code":
            fields[name] = _column_mode(matrix)
            continue
        q10, q25, q50, q75, q90 = (_nanquantile(matrix, q) for q in (0.10, 0.25, 0.50, 0.75, 0.90))
        mean = np.nanmean(matrix, axis=0)
        fields[name] = mean if name in {"temperature_2m", "dew_point_2m", "pressure_msl"} else q50
        stats[name] = {"q10": q10, "q25": q25, "q50": q50, "q75": q75, "q90": q90, "mean": mean}
        if name == "precipitation":
            valid = np.sum(np.isfinite(matrix), axis=0)
            for threshold, suffix in ((0.1, "0p1"), (1.0, "1"), (5.0, "5")):
                events = np.sum(np.where(np.isfinite(matrix), matrix >= threshold, False), axis=0)
                fields[f"precipitation_probability_{suffix}"] = np.divide(events * 100.0, valid, out=np.full(count, np.nan), where=valid > 0)
    fields["precipitation_intensity"] = _precip_intensity(fields.get("precipitation", np.full(count, np.nan)), meta["times"])
    if "precipitation" in stats:
        for stat in ("q10", "q25", "q50", "q75", "q90"):
            stats["precipitation"][f"{stat}_intensity"] = _precip_intensity(stats["precipitation"][stat], meta["times"])
    observed = len(all_ids)
    expected = source.expected_members or observed
    warnings = list(meta.pop("warnings"))
    if observed < expected:
        warnings.append(f"Неполный ансамбль: {observed}/{expected} членов")
    return MeteogramSeries(fields=fields, stats=stats, member_count=observed, expected_member_count=expected, warnings=warnings, **meta)


def _metadata(payload: dict[str, Any], source: MeteogramSource, point_label: str, lat: float, lon: float) -> dict[str, Any]:
    hourly = payload.get("hourly") or {}
    raw_times = hourly.get("time") or []
    if len(raw_times) < 2:
        reason = payload.get("reason") or payload.get("error") or "нет hourly.time"
        raise MeteogramError(f"Некорректный ответ {source.label}: {reason}")
    timezone_name = str(payload.get("timezone") or "UTC")
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name, tz = "UTC", UTC
    times = []
    for value in raw_times:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        times.append(parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz))
    retrieved = _parse_time(payload.get("_retrieved_at_utc"))
    return {
        "source": source, "point_label": point_label,
        "requested_lat": float(lat), "requested_lon": float(lon),
        "grid_lat": _float_or_none(payload.get("latitude")),
        "grid_lon": _float_or_none(payload.get("longitude")),
        "timezone": timezone_name, "times": times,
        "retrieved_at_utc": retrieved, "warnings": [
            "Цикл исходной модели не передан поставщиком; показан период действия прогноза.",
            "Open-Meteo может интерполировать нативные сроки модели к почасовой сетке.",
        ],
    }


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


def _numeric(values: Any, count: int) -> np.ndarray:
    result = np.full(count, np.nan, dtype=float)
    if not isinstance(values, list):
        return result
    for index, value in enumerate(values[:count]):
        try:
            result[index] = float(value) if value is not None else np.nan
        except (TypeError, ValueError):
            pass
    return result


def _matrix(group: dict[int, np.ndarray], count: int) -> np.ndarray:
    return np.vstack([group[key] for key in sorted(group)]) if group else np.empty((0, count), dtype=float)


def _nanquantile(matrix: np.ndarray, q: float) -> np.ndarray:
    with np.errstate(all="ignore"):
        try:
            return np.nanquantile(matrix, q, axis=0, method="median_unbiased")
        except TypeError:
            return np.nanquantile(matrix, q, axis=0)


def _column_mode(matrix: np.ndarray) -> np.ndarray:
    result = np.full(matrix.shape[1], np.nan)
    for index in range(matrix.shape[1]):
        values = matrix[:, index]
        values = np.rint(values[np.isfinite(values)])
        if values.size:
            unique, counts = np.unique(values, return_counts=True)
            result[index] = unique[int(np.argmax(counts))]
    return result


def _circular_mean(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    radians = np.deg2rad(matrix)
    u, v = np.nanmean(np.cos(radians), axis=0), np.nanmean(np.sin(radians), axis=0)
    resultant = np.hypot(u, v)
    direction = (np.rad2deg(np.arctan2(v, u)) + 360.0) % 360.0
    direction[resultant < 0.05] = np.nan
    return direction, resultant


def _precip_intensity(values: np.ndarray, times: list[datetime]) -> np.ndarray:
    intervals = np.ones(len(times), dtype=float)
    if len(times) > 1:
        intervals[0] = max((times[1] - times[0]).total_seconds() / 3600.0, 1.0)
        for index in range(1, len(times)):
            intervals[index] = max((times[index] - times[index - 1]).total_seconds() / 3600.0, 1.0)
    return np.asarray(values, dtype=float) / intervals


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def grid_distance_km(series: MeteogramSeries) -> float | None:
    if series.grid_lat is None or series.grid_lon is None:
        return None
    lat1, lat2 = math.radians(series.requested_lat), math.radians(series.grid_lat)
    dlat = lat2 - lat1
    dlon = math.radians(series.grid_lon - series.requested_lon)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))
