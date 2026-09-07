from __future__ import annotations

"""Adapt WeatherNext 3 BigQuery ensemble statistics to the existing meteogram model."""

import math
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from meteogram_models import MeteogramSeries, source_for_id
from weathernext3_provider import WeatherNext3Provider, provider_from_env

Progress = Callable[[str], None] | None


def _finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _series(rows: list[dict[str, Any]], key: str, *, scale: float = 1.0, offset: float = 0.0) -> np.ndarray:
    values = np.array([_finite(row.get(key)) for row in rows], dtype=float)
    return values * scale + offset


def _station_or_grid(rows: list[dict[str, Any]], station_key: str, grid_key: str) -> np.ndarray:
    station = _series(rows, station_key, offset=-273.15)
    grid = _series(rows, grid_key, offset=-273.15)
    return np.where(np.isfinite(station), station, grid)


def _relative_humidity(temp_c: np.ndarray, dewpoint_c: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        gamma_td = (17.625 * dewpoint_c) / (243.04 + dewpoint_c)
        gamma_t = (17.625 * temp_c) / (243.04 + temp_c)
        rh = 100.0 * np.exp(gamma_td - gamma_t)
    rh[~np.isfinite(temp_c) | ~np.isfinite(dewpoint_c)] = np.nan
    return np.clip(rh, 0.0, 100.0)


def _astronomical_is_day(times: list[Any], lat: float, lon: float) -> np.ndarray:
    latitude = math.radians(float(lat))
    result: list[float] = []
    for value in times:
        dt = _to_datetime(value)
        day = dt.timetuple().tm_yday
        hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
        gamma = 2.0 * math.pi / 365.0 * (day - 1 + (hour - 12.0) / 24.0)
        equation = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) - 0.014615 * math.cos(2.0 * gamma) - 0.040849 * math.sin(2.0 * gamma))
        declination = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2.0 * gamma) + 0.000907 * math.sin(2.0 * gamma) - 0.002697 * math.cos(3.0 * gamma) + 0.00148 * math.sin(3.0 * gamma))
        true_solar_minutes = (hour * 60.0 + equation + 4.0 * float(lon)) % 1440.0
        hour_angle = math.radians(true_solar_minutes / 4.0 - 180.0)
        cos_zenith = math.sin(latitude) * math.sin(declination) + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
        result.append(1.0 if cos_zenith > math.cos(math.radians(90.833)) else 0.0)
    return np.asarray(result, dtype=float)


def _wind_direction(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        result = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    result[~np.isfinite(u) | ~np.isfinite(v)] = np.nan
    return result


def _stat_map(rows: list[dict[str, Any]], prefix: str, *, scale: float = 1.0, offset: float = 0.0, station_prefix: str | None = None) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {}
    for source_suffix, target_suffix in (("p10", "q10"), ("p25", "q25"), ("p50", "q50"), ("p75", "q75"), ("p90", "q90"), ("mean", "mean")):
        if station_prefix:
            station = _series(rows, f"{station_prefix}_{source_suffix}", scale=scale, offset=offset)
            grid = _series(rows, f"{prefix}_{source_suffix}", scale=scale, offset=offset)
            values = np.where(np.isfinite(station), station, grid)
        else:
            values = _series(rows, f"{prefix}_{source_suffix}", scale=scale, offset=offset)
        result[target_suffix] = values
    return result


def fetch_weathernext3_meteogram(point_label: str, lat: float, lon: float, days: int, progress: Progress = None, *, provider: WeatherNext3Provider | None = None) -> MeteogramSeries:
    source = source_for_id("weathernext3")
    days = int(days)
    if days < 1 or days > source.horizon_days:
        raise ValueError(f"Для {source.label} доступно 1–{source.horizon_days} суток")
    if progress:
        progress("Ищу последний опубликованный запуск WeatherNext 3")
    provider = provider or provider_from_env()
    point = provider.point_series(point_label, float(lat), float(lon), days * 24)
    rows = point.rows
    if progress:
        progress("Преобразую статистики 64-членного ансамбля WeatherNext 3")

    times = [_to_datetime(row.get("forecast_time")) for row in rows]
    temperature = _station_or_grid(rows, "station_temperature_mean", "temperature_mean")
    dewpoint = _station_or_grid(rows, "station_dewpoint_mean", "dewpoint_mean")
    u = _series(rows, "wind_u_mean")
    v = _series(rows, "wind_v_mean")
    precipitation = _series(rows, "precip_native_mean", scale=1000.0)
    fields = {
        "temperature_2m": temperature,
        "dew_point_2m": dewpoint,
        "relative_humidity_2m": _relative_humidity(temperature, dewpoint),
        "precipitation": precipitation,
        "precipitation_intensity": precipitation.copy(),
        "pressure_msl": _series(rows, "pressure_mean", scale=0.01),
        "cloud_cover": _series(rows, "cloud_total_mean", scale=100.0),
        "cloud_cover_low": _series(rows, "cloud_low_mean", scale=100.0),
        "cloud_cover_mid": _series(rows, "cloud_mid_mean", scale=100.0),
        "cloud_cover_high": _series(rows, "cloud_high_mean", scale=100.0),
        "wind_speed_10m": _series(rows, "wind_speed_mean"),
        "wind_direction_10m": _wind_direction(u, v),
        "wind_gusts_10m": np.full(len(rows), np.nan, dtype=float),
        "weather_code": np.full(len(rows), np.nan, dtype=float),
        "is_day": _astronomical_is_day(times, float(lat), float(lon)),
        "ensemble_member_count": np.full(len(rows), 64.0, dtype=float),
        "precipitation_imerg": _series(rows, "precip_imerg_mean", scale=1000.0),
        "precipitation_experimental": _series(rows, "precip_experimental_mean", scale=1000.0),
        "surface_solar_radiation_1hr": _series(rows, "solar_mean"),
    }
    stats = {
        "temperature_2m": _stat_map(rows, "temperature", offset=-273.15, station_prefix="station_temperature"),
        "dew_point_2m": _stat_map(rows, "dewpoint", offset=-273.15, station_prefix="station_dewpoint"),
        "precipitation": _stat_map(rows, "precip_native", scale=1000.0),
        "wind_speed_10m": _stat_map(rows, "wind_speed"),
    }
    for key in ("q10", "q25", "q50", "q75", "q90", "mean"):
        stats["precipitation"][f"{key}_intensity"] = stats["precipitation"][key].copy()
    warnings = [
        "WeatherNext 3 BigQuery: готовые статистики 64-членного ансамбля, без загрузки отдельных членов",
        "T/Td используют station head 0.05° при наличии; остальные поля — сетка 0.1°",
        "Порывы и weather code в BigQuery surface statistics отсутствуют",
    ]
    return MeteogramSeries(
        source=source,
        point_label=str(point_label), requested_lat=float(lat), requested_lon=float(lon),
        grid_lat=point.station_grid_lat if point.station_grid_lat is not None else point.grid_lat,
        grid_lon=point.station_grid_lon if point.station_grid_lon is not None else point.grid_lon,
        timezone="UTC", times=times, fields=fields, stats=stats,
        retrieved_at_utc=datetime.now(timezone.utc), member_count=64, expected_member_count=64,
        warnings=warnings, init_time_utc=point.run.init_time_utc,
    )


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)
