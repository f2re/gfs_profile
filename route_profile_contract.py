from __future__ import annotations

"""Доменный контракт маршрутного профиля.

Риск является свойством модельных данных и маршрута. Режим ``simple``/``pro``
влияет только на представление: исходные точки, сроки, маски и категории риска
обязаны совпадать. Пространственная дискретизация ориентирована на масштаб
сетки GFS 0.25° — целевой шаг 25 км.
"""

import inspect
import math
from typing import Any

import numpy as np

import route_profile as _route
import route_profile_plot as _plot
from geocode import GeoPoint
from gfs_core import GfsProfileError

ROUTE_SPATIAL_STEP_KM = 25.0
# 161 точка сохраняет шаг <=25 км для маршрутов длиной до 4000 км.
# На более длинном маршруте число запросов ограничивается, а фактический шаг
# честно вычисляется и выводится в сводке.
ROUTE_MAX_POINTS = 161
ROUTE_RISK_CARD_LIMIT = 12


def route_waypoint_specs(
    origin: GeoPoint,
    destination: GeoPoint,
    departure_lead: int,
    speed_kmh: int = _route.ROUTE_DEFAULT_SPEED_KMH,
    max_points: int = ROUTE_MAX_POINTS,
    spatial_step_km: float = ROUTE_SPATIAL_STEP_KM,
) -> tuple[float, float, list[tuple[float, float, float, float, int]]]:
    """Построить точки маршрута по расстоянию, а срок — по ETA.

    Точки не зависят от режима визуализации. До эксплуатационного лимита
    расстояние между соседними точками не превышает ``spatial_step_km``.
    """

    if not _route.ROUTE_MIN_SPEED_KMH <= int(speed_kmh) <= _route.ROUTE_MAX_SPEED_KMH:
        raise GfsProfileError(
            f"Скорость маршрута должна быть {_route.ROUTE_MIN_SPEED_KMH}…{_route.ROUTE_MAX_SPEED_KMH} км/ч"
        )
    if not math.isfinite(spatial_step_km) or spatial_step_km <= 0:
        raise GfsProfileError("Пространственный шаг маршрута должен быть положительным")

    distance = _route.haversine_km(origin.lat, origin.lon, destination.lat, destination.lon)
    if distance < 5.0:
        raise GfsProfileError("Начало и конец маршрута почти совпадают; задайте маршрут длиннее 5 км")
    duration = distance / float(speed_kmh)

    requested_segments = max(1, int(math.ceil(distance / float(spatial_step_km))))
    segment_count = min(requested_segments, max(1, int(max_points) - 1))

    specs: list[tuple[float, float, float, float, int]] = []
    for segment_index in range(segment_count + 1):
        fraction = segment_index / float(segment_count)
        distance_km = distance * fraction
        elapsed_hours = distance_km / float(speed_kmh)
        lat, lon = _route.great_circle_point(
            origin.lat,
            origin.lon,
            destination.lat,
            destination.lon,
            fraction,
        )
        lead = _route.normalize_eta_lead(float(departure_lead) + elapsed_hours)
        specs.append((fraction, lat, lon, distance_km, lead))
    return distance, duration, specs


def spatial_step_km(data: Any) -> float:
    waypoints = tuple(data.waypoints)
    if len(waypoints) < 2:
        return 0.0
    distances = np.asarray([point.distance_km for point in waypoints], dtype=float)
    return float(np.nanmax(np.diff(distances)))


def risk_signature(data: Any) -> tuple[tuple[int, ...], bytes, bytes, bytes]:
    """Вернуть объективные поля риска без учёта режима отображения."""

    return (
        tuple(int(value) for value in np.asarray(data.point_risk, dtype=int).tolist()),
        np.asarray(data.icing_score, dtype=np.int8).tobytes(),
        np.asarray(data.turbulence_score, dtype=np.int8).tobytes(),
        np.asarray(data.cloud_mask, dtype=np.bool_).tobytes(),
    )


_original_build_route_profile_data = _route.build_route_profile_data
_original_route_summary = _route.route_summary


def build_route_profile_data(
    run,
    origin: GeoPoint,
    destination: GeoPoint,
    departure_lead: int,
    speed_kmh: int = _route.ROUTE_DEFAULT_SPEED_KMH,
    mode: str = "simple",
    progress_callback=None,
    max_points: int = ROUTE_MAX_POINTS,
):
    """Вызвать единый расчёт с пространственным шагом GFS-масштаба."""

    return _original_build_route_profile_data(
        run,
        origin,
        destination,
        departure_lead,
        speed_kmh=speed_kmh,
        mode=mode,
        progress_callback=progress_callback,
        max_points=max_points,
    )


def route_summary(data) -> str:
    text = _original_route_summary(data)
    needle = f" · точек {len(data.waypoints)}\n"
    replacement = (
        f" · точек {len(data.waypoints)} · пространственный шаг ≈{spatial_step_km(data):.0f} км\n"
    )
    return text.replace(needle, replacement, 1)


# Patch before telegram_route imports symbols from route_profile.
_route.ROUTE_SPATIAL_STEP_KM = ROUTE_SPATIAL_STEP_KM
_route.ROUTE_MAX_POINTS = ROUTE_MAX_POINTS
_route.route_waypoint_specs = route_waypoint_specs
_route.build_route_profile_data = build_route_profile_data
_route.route_summary = route_summary

if not hasattr(_route.RouteProfileData, "spatial_step_km"):
    _route.RouteProfileData.spatial_step_km = property(spatial_step_km)  # type: ignore[attr-defined]
if not hasattr(_route.RouteProfileData, "risk_signature"):
    _route.RouteProfileData.risk_signature = property(risk_signature)  # type: ignore[attr-defined]

# Одни и те же границы карточек и одна агрегация риска в обоих режимах.
_plot._MAX_SIMPLE_CARDS = ROUTE_RISK_CARD_LIMIT
_plot._MAX_PRO_CARDS = ROUTE_RISK_CARD_LIMIT

# Guard against accidental drift of the wrapper default.
assert inspect.signature(build_route_profile_data).parameters["max_points"].default == ROUTE_MAX_POINTS
