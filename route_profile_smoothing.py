from __future__ import annotations

"""Display-only resampling for the route profile.

The objective risk and all exported values remain on the source GFS grid.
This module creates a separate display grid used only by the PNG renderer.
"""

from dataclasses import dataclass
import math

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import binary_closing, binary_opening, gaussian_filter

from route_profile import RouteProfileData


@dataclass(frozen=True)
class RouteDisplayGrid:
    x_km: np.ndarray
    pressure_hpa: np.ndarray
    temperature_c: np.ndarray
    humidity_pct: np.ndarray
    wind_speed_ms: np.ndarray
    u_wind_ms: np.ndarray
    v_wind_ms: np.ndarray
    along_track_wind_ms: np.ndarray
    cloud_intensity: np.ndarray
    icing_intensity: np.ndarray
    turbulence_intensity: np.ndarray

    @property
    def cloud_mask(self) -> np.ndarray:
        return self.cloud_intensity >= 0.38

    @property
    def icing_mask(self) -> np.ndarray:
        return self.icing_intensity >= 0.38

    @property
    def turbulence_mask(self) -> np.ndarray:
        return self.turbulence_intensity >= 0.38


def _interpolate_rows(values: np.ndarray, src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    array = np.asarray(values, dtype=float)
    result = np.full((array.shape[0], dst.size), np.nan, dtype=float)
    order = np.argsort(src)
    src_sorted = src[order]

    for row_index, row in enumerate(array):
        row_sorted = row[order]
        valid = np.isfinite(src_sorted) & np.isfinite(row_sorted)
        count = int(np.sum(valid))
        if count == 0:
            continue
        if count == 1:
            result[row_index, :] = float(row_sorted[valid][0])
            continue
        x_valid = src_sorted[valid]
        y_valid = row_sorted[valid]
        interpolator = PchipInterpolator(x_valid, y_valid, extrapolate=False)
        interpolated = np.asarray(interpolator(dst), dtype=float)
        interpolated[dst < x_valid[0]] = y_valid[0]
        interpolated[dst > x_valid[-1]] = y_valid[-1]
        result[row_index, :] = interpolated
    return result


def _resample_field(
    values: np.ndarray,
    x_source: np.ndarray,
    x_target: np.ndarray,
    pressure_source: np.ndarray,
    pressure_target: np.ndarray,
) -> np.ndarray:
    horizontal = _interpolate_rows(np.asarray(values, dtype=float), x_source, x_target)
    vertical = _interpolate_rows(horizontal.T, pressure_source, pressure_target).T
    return vertical


def _smooth_nan(values: np.ndarray, sigma: tuple[float, float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    valid = np.isfinite(array)
    if not valid.any():
        return np.full_like(array, np.nan, dtype=float)
    numerator = gaussian_filter(np.where(valid, array, 0.0), sigma=sigma, mode="nearest")
    denominator = gaussian_filter(valid.astype(float), sigma=sigma, mode="nearest")
    return np.divide(numerator, denominator, out=np.full_like(array, np.nan), where=denominator > 1e-5)


def _clean_probability(values: np.ndarray, *, sigma: tuple[float, float], close_iterations: int) -> np.ndarray:
    smoothed = _smooth_nan(np.asarray(values, dtype=float), sigma)
    smoothed = np.nan_to_num(smoothed, nan=0.0)
    initial = smoothed >= 0.34
    if close_iterations > 0:
        structure = np.ones((3, 3), dtype=bool)
        initial = binary_closing(initial, structure=structure, iterations=close_iterations)
        initial = binary_opening(initial, structure=structure, iterations=1)
        smoothed = np.maximum(
            smoothed,
            gaussian_filter(initial.astype(float), sigma=(0.8, 1.2), mode="nearest") * 0.72,
        )
    return np.clip(smoothed, 0.0, 1.0)


def _forward_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def route_bearings_deg(data: RouteProfileData) -> np.ndarray:
    points = tuple(data.waypoints)
    if len(points) <= 1:
        return np.zeros(len(points), dtype=float)
    segment = np.asarray(
        [
            _forward_bearing_deg(
                points[index].lat,
                points[index].lon,
                points[index + 1].lat,
                points[index + 1].lon,
            )
            for index in range(len(points) - 1)
        ],
        dtype=float,
    )
    bearings = np.empty(len(points), dtype=float)
    bearings[0] = segment[0]
    bearings[-1] = segment[-1]
    if len(points) > 2:
        left = np.radians(segment[:-1])
        right = np.radians(segment[1:])
        bearings[1:-1] = np.degrees(
            np.arctan2(np.sin(left) + np.sin(right), np.cos(left) + np.cos(right))
        ) % 360.0
    return bearings


def _display_shape(data: RouteProfileData, mode: str) -> tuple[int, int]:
    distance = max(1.0, float(data.total_distance_km))
    if mode == "simple":
        x_count = int(np.clip(math.ceil(distance / 5.0) + 1, 180, 480))
        pressure_count = 101
    else:
        x_count = max(
            len(data.waypoints),
            int(np.clip(math.ceil(distance / 10.0) + 1, 120, 360)),
        )
        pressure_count = 73
    return x_count, pressure_count


def build_route_display_grid(data: RouteProfileData, mode: str | None = None) -> RouteDisplayGrid:
    mode_name = (mode or data.mode).strip().lower()
    if mode_name not in {"simple", "pro"}:
        raise ValueError("mode must be simple or pro")

    x_source = np.asarray([point.distance_km for point in data.waypoints], dtype=float)
    pressure_source = np.asarray(data.levels_hpa, dtype=float)
    x_count, pressure_count = _display_shape(data, mode_name)
    x_target = np.linspace(0.0, max(1.0, float(data.total_distance_km)), x_count)
    pressure_target = np.linspace(
        float(np.max(pressure_source)),
        float(np.min(pressure_source)),
        pressure_count,
    )

    fields = {
        "temperature": _resample_field(data.temperature_c, x_source, x_target, pressure_source, pressure_target),
        "humidity": _resample_field(data.humidity_pct, x_source, x_target, pressure_source, pressure_target),
        "wind": _resample_field(data.wind_speed_ms, x_source, x_target, pressure_source, pressure_target),
        "u": _resample_field(data.u_wind_ms, x_source, x_target, pressure_source, pressure_target),
        "v": _resample_field(data.v_wind_ms, x_source, x_target, pressure_source, pressure_target),
        "cloud_source": _resample_field(
            data.cloud_mask.astype(float), x_source, x_target, pressure_source, pressure_target
        ),
        "icing_source": _resample_field(
            np.asarray(data.icing_score, dtype=float) / 3.0,
            x_source,
            x_target,
            pressure_source,
            pressure_target,
        ),
        "turbulence_source": _resample_field(
            np.asarray(data.turbulence_score, dtype=float) / 3.0,
            x_source,
            x_target,
            pressure_source,
            pressure_target,
        ),
    }

    bearings = np.radians(route_bearings_deg(data))
    along_source = (
        data.u_wind_ms * np.sin(bearings)[None, :]
        + data.v_wind_ms * np.cos(bearings)[None, :]
    )
    fields["along"] = _resample_field(
        along_source, x_source, x_target, pressure_source, pressure_target
    )

    if mode_name == "simple":
        scalar_sigma = (1.25, 2.4)
        wind_sigma = (0.9, 1.8)
        probability_sigma = (1.35, 2.8)
        close_iterations = 2
    else:
        scalar_sigma = (0.35, 0.75)
        wind_sigma = (0.3, 0.6)
        probability_sigma = (0.45, 0.85)
        close_iterations = 0

    temperature = _smooth_nan(fields["temperature"], scalar_sigma)
    humidity = np.clip(_smooth_nan(fields["humidity"], scalar_sigma), 0.0, 100.0)
    wind_speed = np.maximum(0.0, _smooth_nan(fields["wind"], wind_sigma))
    u_wind = _smooth_nan(fields["u"], wind_sigma)
    v_wind = _smooth_nan(fields["v"], wind_sigma)
    along_wind = _smooth_nan(fields["along"], wind_sigma)

    rh_cloud = np.clip((humidity - 74.0) / 22.0, 0.0, 1.0)
    cloud_seed = np.maximum(fields["cloud_source"] * 0.82, rh_cloud)
    cloud_intensity = _clean_probability(
        cloud_seed,
        sigma=probability_sigma,
        close_iterations=close_iterations,
    )
    icing_intensity = _clean_probability(
        fields["icing_source"],
        sigma=probability_sigma,
        close_iterations=close_iterations,
    )
    turbulence_intensity = _clean_probability(
        fields["turbulence_source"],
        sigma=probability_sigma,
        close_iterations=close_iterations,
    )

    return RouteDisplayGrid(
        x_km=x_target,
        pressure_hpa=pressure_target,
        temperature_c=temperature,
        humidity_pct=humidity,
        wind_speed_ms=wind_speed,
        u_wind_ms=u_wind,
        v_wind_ms=v_wind,
        along_track_wind_ms=along_wind,
        cloud_intensity=cloud_intensity,
        icing_intensity=icing_intensity,
        turbulence_intensity=turbulence_intensity,
    )
