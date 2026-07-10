from __future__ import annotations

"""Доменный контракт маршрутного профиля.

Риск является свойством модельных данных в конкретной точке и на конкретном
сроке. Режим ``simple``/``pro`` влияет только на представление. Пространственный
шаг выбирает пользователь; ETA каждой точки рассчитывается из расстояния и
средней скорости.
"""

import inspect
import math
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Iterable

import numpy as np

import route_profile as _route
import route_profile_plot as _plot
from geocode import GeoPoint
from gfs_core import GfsProfileError

ROUTE_SPATIAL_STEP_KM = 25.0
ROUTE_SPATIAL_STEPS_KM = (25, 50, 100)
ROUTE_MAX_POINTS = 161
ROUTE_RISK_CARD_LIMIT = 12
_ROUTE_STEP_CONTEXT: ContextVar[float] = ContextVar("route_spatial_step_km", default=ROUTE_SPATIAL_STEP_KM)


def validate_spatial_step(value: float | int) -> int:
    step = int(value)
    if step not in ROUTE_SPATIAL_STEPS_KM:
        allowed = "/".join(str(item) for item in ROUTE_SPATIAL_STEPS_KM)
        raise GfsProfileError(f"Шаг маршрута должен быть {allowed} км")
    return step


def route_waypoint_specs(
    origin: GeoPoint,
    destination: GeoPoint,
    departure_lead: int,
    speed_kmh: int = _route.ROUTE_DEFAULT_SPEED_KMH,
    max_points: int = ROUTE_MAX_POINTS,
    spatial_step_km: float | None = None,
) -> tuple[float, float, list[tuple[float, float, float, float, int]]]:
    """Построить точки по расстоянию, а срок каждой точки — по ETA."""

    if not _route.ROUTE_MIN_SPEED_KMH <= int(speed_kmh) <= _route.ROUTE_MAX_SPEED_KMH:
        raise GfsProfileError(
            f"Скорость маршрута должна быть {_route.ROUTE_MIN_SPEED_KMH}…{_route.ROUTE_MAX_SPEED_KMH} км/ч"
        )
    step = float(_ROUTE_STEP_CONTEXT.get() if spatial_step_km is None else spatial_step_km)
    if not math.isfinite(step) or step <= 0:
        raise GfsProfileError("Пространственный шаг маршрута должен быть положительным")

    distance = _route.haversine_km(origin.lat, origin.lon, destination.lat, destination.lon)
    if distance < 5.0:
        raise GfsProfileError("Начало и конец маршрута почти совпадают; задайте маршрут длиннее 5 км")
    duration = distance / float(speed_kmh)

    requested_segments = max(1, int(math.ceil(distance / step)))
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


def _finite_max(values: np.ndarray, default: float = 0.0) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.max(finite)) if finite.size else float(default)


def confirmed_thunder(surface: Any) -> bool:
    return str(getattr(surface, "phenomena", "")) == "TSRA"


def vertical_risk_for_point(data: Any, point_index: int) -> int:
    icing = _finite_max(data.icing_score[:, point_index])
    turbulence = _finite_max(data.turbulence_score[:, point_index])
    wind = _finite_max(data.wind_speed_ms[:, point_index])
    if icing >= 3 or turbulence >= 3:
        return 3
    if icing >= 2 or turbulence >= 2 or wind >= 30.0:
        return 2
    if icing >= 1 or turbulence >= 1 or wind >= 20.0:
        return 1
    return 0


def surface_risk(surface: Any) -> int:
    score = 0
    precip = getattr(surface, "precip_mm", None)
    ceiling = getattr(surface, "ceiling_m", None)
    visibility = getattr(surface, "visibility_km", None)
    cb_score = int(getattr(surface, "cb_score", 0) or 0)
    if precip is not None and float(precip) >= 0.2:
        score = max(score, 1)
    if precip is not None and float(precip) >= 7.0:
        score = max(score, 2)
    if ceiling is not None and float(ceiling) < 1000.0:
        score = max(score, 2)
    if ceiling is not None and float(ceiling) < 300.0:
        score = max(score, 3)
    if visibility is not None and float(visibility) < 5.0:
        score = max(score, 2)
    if visibility is not None and float(visibility) < 1.0:
        score = max(score, 3)
    if cb_score >= 2:
        score = max(score, 2)
    if confirmed_thunder(surface):
        score = 3
    return score


def point_risk_reasons(data: Any, point_index: int) -> tuple[str, ...]:
    point = data.waypoints[point_index]
    surface = point.surface
    reasons: list[str] = []
    if confirmed_thunder(surface):
        reasons.append("гроза")
    elif int(getattr(surface, "cb_score", 0) or 0) >= 2:
        reasons.append("конвективный риск")

    phenomena = str(getattr(surface, "phenomena", "—") or "—")
    if phenomena not in {"—", "TSRA"}:
        reasons.append(phenomena)
    visibility = getattr(surface, "visibility_km", None)
    ceiling = getattr(surface, "ceiling_m", None)
    precip = getattr(surface, "precip_mm", None)
    if visibility is not None and float(visibility) < 5.0:
        reasons.append("видимость <5 км")
    if ceiling is not None and float(ceiling) < 1000.0:
        reasons.append("низкий ВНГО")
    if precip is not None and float(precip) >= 7.0:
        reasons.append("сильные осадки")
    elif precip is not None and float(precip) >= 0.2:
        reasons.append("осадки")

    icing = _finite_max(data.icing_score[:, point_index])
    turbulence = _finite_max(data.turbulence_score[:, point_index])
    wind = _finite_max(data.wind_speed_ms[:, point_index])
    if icing >= 2:
        reasons.append("обледенение")
    elif icing >= 1:
        reasons.append("риск обледенения")
    if turbulence >= 2:
        reasons.append("сильный сдвиг/болтанка")
    elif turbulence >= 1:
        reasons.append("сдвиг ветра")
    if wind >= 30.0:
        reasons.append("ветер ≥30 м/с")
    elif wind >= 20.0:
        reasons.append("сильный ветер")
    if not reasons:
        reasons.append("значимых модельных рисков нет")
    return tuple(dict.fromkeys(reasons))


def recompute_objective_risk(data: Any) -> Any:
    scores = np.zeros(len(data.waypoints), dtype=int)
    waypoints = []
    for point_index, point in enumerate(data.waypoints):
        score = max(vertical_risk_for_point(data, point_index), surface_risk(point.surface))
        scores[point_index] = score
        waypoints.append(replace(point, risk_score=score, risk_reasons=point_risk_reasons(data, point_index)))
    return replace(data, waypoints=tuple(waypoints), point_risk=scores)


def risk_signature(data: Any) -> tuple[Any, ...]:
    return (
        tuple(int(value) for value in np.asarray(data.point_risk, dtype=int).tolist()),
        np.asarray(data.icing_score, dtype=np.int8).tobytes(),
        np.asarray(data.turbulence_score, dtype=np.int8).tobytes(),
        np.asarray(data.cloud_mask, dtype=np.bool_).tobytes(),
        tuple(str(point.surface.phenomena) for point in data.waypoints),
        tuple(int(point.surface.cb_score) for point in data.waypoints),
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
    spatial_step_km: int = int(ROUTE_SPATIAL_STEP_KM),
):
    step = validate_spatial_step(spatial_step_km)
    token = _ROUTE_STEP_CONTEXT.set(float(step))
    try:
        data = _original_build_route_profile_data(
            run,
            origin,
            destination,
            departure_lead,
            speed_kmh=speed_kmh,
            mode=mode,
            progress_callback=progress_callback,
            max_points=max_points,
        )
    finally:
        _ROUTE_STEP_CONTEXT.reset(token)
    return recompute_objective_risk(data)


def route_summary(data) -> str:
    text = _original_route_summary(data)
    needle = f" · точек {len(data.waypoints)}\n"
    replacement = f" · точек {len(data.waypoints)} · пространственный шаг ≈{spatial_step_km(data):.0f} км\n"
    return text.replace(needle, replacement, 1)


def _hazard_tokens_for_indices(data: Any, indices: Iterable[int], limit: int = 3):
    selected = tuple(sorted(set(int(index) for index in indices)))
    if not selected:
        return ()
    points = [data.waypoints[index] for index in selected]
    active = {
        "thunder": any(confirmed_thunder(point.surface) for point in points),
        "icing": _finite_max(data.icing_score[:, selected]) >= 1,
        "turbulence": _finite_max(data.turbulence_score[:, selected]) >= 1,
        "wind": _finite_max(data.wind_speed_ms[:, selected]) >= 20.0,
        "visibility": any(
            (point.surface.visibility_km is not None and point.surface.visibility_km < 5.0)
            or (point.surface.ceiling_m is not None and point.surface.ceiling_m < 1000.0)
            for point in points
        ),
        "precip": any((point.surface.precip_mm or 0.0) >= 0.2 for point in points),
        "cloud": bool(np.any(data.cloud_mask[:, selected])),
    }
    tokens = []
    for key in ("thunder", "icing", "turbulence", "wind", "visibility", "precip", "cloud"):
        if active[key] and not (key == "precip" and active["thunder"]):
            tokens.append(_plot._HAZARD_TOKENS[key])
        if len(tokens) >= max(1, int(limit)):
            break
    return tuple(tokens)


def _draw_surface_symbols(ax, data: Any, x: np.ndarray, *, professional: bool) -> None:
    thunder_mask = np.asarray([confirmed_thunder(point.surface) for point in data.waypoints], dtype=bool)
    precip_mask = np.asarray([(point.surface.precip_mm or 0.0) >= 0.2 for point in data.waypoints], dtype=bool) & ~thunder_mask
    for mask, symbol, label, color, y in (
        (thunder_mask, "⚡", "ГРОЗА", _plot.AVIATION.convection, 960.0),
        (precip_mask, "●", "ОСАДКИ", "#3F73B8", 975.0),
    ):
        for start, end in _plot._mask_runs(mask)[:4]:
            center_x = float(np.mean(x[start : end + 1]))
            ax.text(
                center_x,
                y,
                f"{symbol} {label}" if professional else symbol,
                ha="center",
                va="center",
                fontsize=8.0 if professional else 12.0,
                fontweight="bold",
                color=color,
                zorder=12,
                bbox={"boxstyle": "round,pad=0.20", "fc": "white", "ec": color, "lw": 0.8, "alpha": 0.92},
            )


_route.ROUTE_SPATIAL_STEP_KM = ROUTE_SPATIAL_STEP_KM
_route.ROUTE_MAX_POINTS = ROUTE_MAX_POINTS
_route.route_waypoint_specs = route_waypoint_specs
_route.build_route_profile_data = build_route_profile_data
_route.route_summary = route_summary

if not hasattr(_route.RouteProfileData, "spatial_step_km"):
    _route.RouteProfileData.spatial_step_km = property(spatial_step_km)  # type: ignore[attr-defined]
if not hasattr(_route.RouteProfileData, "risk_signature"):
    _route.RouteProfileData.risk_signature = property(risk_signature)  # type: ignore[attr-defined]

_plot._MAX_SIMPLE_CARDS = ROUTE_RISK_CARD_LIMIT
_plot._MAX_PRO_CARDS = ROUTE_RISK_CARD_LIMIT
_plot._hazard_tokens_for_indices = _hazard_tokens_for_indices
_plot._draw_surface_symbols = _draw_surface_symbols

assert inspect.signature(build_route_profile_data).parameters["max_points"].default == ROUTE_MAX_POINTS
