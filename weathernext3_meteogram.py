from __future__ import annotations

from feature_flags import require_weathernext3

"""Adapt WeatherNext 3 BigQuery ensemble statistics to the existing meteogram model."""

import math
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from meteogram_models import MeteogramSeries, source_for_id
from weathernext3_provider import WeatherNext3Provider, provider_from_env
from weathernext3_surface import surface_rows
from weathernext3_math import wind_from, relative_humidity

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


_relative_humidity = relative_humidity


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


_wind_direction = wind_from


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


def fetch_weathernext3_meteogram(point_label: str, lat: float, lon: float, days: int, progress: Progress = None, *, provider: WeatherNext3Provider | None = None, view: str = "ensemble") -> MeteogramSeries:
    require_weathernext3()
    if view not in {"mean", "ensemble"}:
        raise ValueError("Вид метеограммы WN3: mean или ensemble")
    source = source_for_id("weathernext3_mean" if view == "mean" else "weathernext3")
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

    converted = surface_rows(point)
    def field(key):
        return np.array([row.get(key, np.nan) for row in converted], dtype=float)
    times = [row['valid_utc'] for row in converted]
    temperature = field('temperature_mean_c')
    dewpoint = field('dewpoint_mean_c')
    precipitation = field('precip_native_mean_mm')
    fields = {
        "temperature_2m": temperature, "dew_point_2m": dewpoint,
        "relative_humidity_2m": field('rh_from_mean_pct'),
        "precipitation": precipitation, "precipitation_intensity": precipitation.copy(),
        "pressure_msl": field('pressure_msl_hpa'),
        "cloud_cover": field('cloud_total_pct'), "cloud_cover_low": field('cloud_low_pct'),
        "cloud_cover_mid": field('cloud_mid_pct'), "cloud_cover_high": field('cloud_high_pct'),
        "wind_speed_10m": field('wind_speed_mean_ms'),
        "wind_direction_10m": field('wind_from_mean_vector_deg'),
        "wind_gusts_10m": np.full(len(rows), np.nan), "weather_code": np.full(len(rows), np.nan),
        "is_day": _astronomical_is_day(times, float(lat), float(lon)),
        # BigQuery does not provide per-time valid-member counts.
        "ensemble_member_count": np.full(len(rows), np.nan),
        "precipitation_imerg": field('precip_imerg_mean_mm'),
        "precipitation_experimental": field('precip_experimental_mean_mm'),
        "surface_solar_radiation_1hr": field('solar_mean_wm2') * 3600,
    }
    stats = {}
    for target, prefix, unit in (('temperature_2m', 'temperature', 'c'), ('dew_point_2m', 'dewpoint', 'c'),
                                  ('precipitation', 'precip_native', 'mm'), ('wind_speed_10m', 'wind_speed', 'ms')):
        stats[target] = {('q' + suffix[1:] if suffix.startswith('p') else suffix): field(f'{prefix}_{suffix}_{unit}')
                         for suffix in ('mean', 'p10', 'p25', 'p50', 'p75', 'p90')}

    for key in ("q10", "q25", "q50", "q75", "q90", "mean"):
        stats["precipitation"][f"{key}_intensity"] = stats["precipitation"][key].copy()
    warnings = list(getattr(point, "warnings", [])) + [
        "WeatherNext 3 BigQuery: готовые статистики 64-членного ансамбля, без загрузки отдельных членов",
        "T/Td используют station head 0.05° при наличии; остальные поля — сетка 0.1°",
        "Порывы и weather code отсутствуют; RH из средних T/Td — диагностическая оценка, не среднее RH",
        "Период от init; число доступных членов и вероятности событий BigQuery не передаёт",
    ]
    if view == "mean":
        stats = {}
        warnings.insert(0, "Показано среднее ансамбля без полос разброса; это не отдельный детерминированный запуск")
    return MeteogramSeries(
        source=source,
        point_label=str(point_label), requested_lat=float(lat), requested_lon=float(lon),
        grid_lat=point.grid_lat, grid_lon=point.grid_lon,
        timezone="UTC", times=times, fields=fields, stats=stats,
        retrieved_at_utc=datetime.now(timezone.utc), member_count=None, expected_member_count=64, ensemble_statistics_only=True,
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
