"""One surface-unit and station-head selection contract for all WN3 outputs."""
from __future__ import annotations

import math
from typing import Any

from weathernext3_math import relative_humidity, wind_from
from weathernext3_provider import WeatherNext3PointSeries, _utc

STATS = ('mean', 'p10', 'p25', 'p50', 'p75', 'p90')


def number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def surface_rows(point: WeatherNext3PointSeries) -> list[dict[str, Any]]:
    """Never mix station and grid T/Td or individual quantiles within one hour."""
    result = []
    # Keep the entire T/Td series on one grid; otherwise fallback can create artificial jumps.
    station = point.station_grid_lat is not None and point.station_grid_lon is not None
    station = station and all(math.isfinite(number(raw.get(f'station_{var}_{stat}')))
                              for raw in point.rows for var in ('temperature', 'dewpoint') for stat in STATS)
    for raw in point.rows:
        prefix = 'station_' if station else ''
        row = {'run_utc': point.run.init_time_utc.isoformat(), 'valid_utc': _utc(raw['forecast_time']),
               'lead_hour': int(raw['forecast_hour']), 'requested_lat': point.requested_lat,
               'requested_lon': point.requested_lon, 'surface_grid_lat': point.grid_lat, 'surface_grid_lon': point.grid_lon,
               'temperature_grid_lat': point.station_grid_lat if station else point.grid_lat,
               'temperature_grid_lon': point.station_grid_lon if station else point.grid_lon,
               'temperature_source': 'station_head_0.05' if station else 'surface_0.1'}
        for stat in STATS:
            row[f'temperature_{stat}_c'] = number(raw.get(f'{prefix}temperature_{stat}')) - 273.15
            row[f'dewpoint_{stat}_c'] = number(raw.get(f'{prefix}dewpoint_{stat}')) - 273.15
            row[f'wind_speed_{stat}_ms'] = number(raw.get(f'wind_speed_{stat}'))
            row[f'precip_native_{stat}_mm'] = number(raw.get(f'precip_native_{stat}')) * 1000
        row['rh_from_mean_pct'] = float(relative_humidity(row['temperature_mean_c'], row['dewpoint_mean_c']))
        row['wind_from_mean_vector_deg'] = float(wind_from(number(raw.get('wind_u_mean')), number(raw.get('wind_v_mean'))))
        row['pressure_msl_hpa'] = number(raw.get('pressure_mean')) / 100
        for layer in ('total', 'low', 'mid', 'high'):
            row[f'cloud_{layer}_pct'] = number(raw.get(f'cloud_{layer}_mean')) * 100
        row['precip_imerg_mean_mm'] = number(raw.get('precip_imerg_mean')) * 1000
        row['precip_experimental_mean_mm'] = number(raw.get('precip_experimental_mean')) * 1000
        row['solar_mean_wm2'] = number(raw.get('solar_mean')) / 3600
        result.append(row)
    return result
