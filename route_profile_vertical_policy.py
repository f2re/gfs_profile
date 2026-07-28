from __future__ import annotations

"""Meteorologically constrained route icing/turbulence diagnostics.

The resulting 0..3 values are transparent GFS proxies. They are not certified
icing categories and not EDR/GTG turbulence. ``install()`` is explicit because
``route_profile`` historically imports this module during a circular import.
"""

from dataclasses import replace
from typing import Any

import numpy as np

from diagnostic_profile import add_profile_diagnostics, build_diagnostic_profile


_INSTALLED = False


def _has_run(values: np.ndarray, threshold: int, minimum_length: int) -> bool:
    mask = np.asarray(values, dtype=float) >= float(threshold)
    run = 0
    for active in mask:
        run = run + 1 if active else 0
        if run >= minimum_length:
            return True
    return False


def shear_severity(shear_ms_per_km: float) -> int:
    """Fallback shear-only bands; the final diagnosis also requires Ri."""

    value = max(0.0, float(shear_ms_per_km))
    if value >= 15.0:
        return 3
    if value >= 10.0:
        return 2
    if value >= 6.0:
        return 1
    return 0


def vertical_risk_for_point(data: Any, point_index: int) -> int:
    icing = np.asarray(data.icing_score[:, point_index], dtype=float)
    turbulence = np.asarray(data.turbulence_score[:, point_index], dtype=float)
    wind_values = np.asarray(data.wind_speed_ms[:, point_index], dtype=float)
    finite_wind = wind_values[np.isfinite(wind_values)]
    max_wind = float(np.max(finite_wind)) if finite_wind.size else 0.0

    max_icing = float(np.nanmax(icing)) if icing.size else 0.0
    max_turbulence = float(np.nanmax(turbulence)) if turbulence.size else 0.0

    # Icing is evaluated at pressure levels; two adjacent high values indicate a
    # layer. Turbulence is a derivative/layer quantity and needs three nodes
    # (two adjacent intervals) before it is promoted to R3.
    severe_persistent = _has_run(icing, 3, 2) or _has_run(turbulence, 3, 3)
    moderate_persistent = _has_run(icing, 2, 2) or _has_run(turbulence, 2, 3)

    if severe_persistent:
        return 3
    if max_icing >= 3 or max_turbulence >= 3 or moderate_persistent or max_wind >= 30.0:
        return 2
    if max_icing >= 1 or max_turbulence >= 1 or max_wind >= 20.0:
        return 1
    return 0


def _precip_equivalent_rate(surface: Any) -> float | None:
    amount = getattr(surface, "precip_mm", None)
    if amount is None:
        return None
    interval = max(1e-6, float(getattr(surface, "precip_interval_hours", 1.0) or 1.0))
    return max(0.0, float(amount)) / interval


def surface_risk(surface: Any) -> int:
    score = 0
    precip_rate = _precip_equivalent_rate(surface)
    ceiling = getattr(surface, "ceiling_m", None)
    visibility = getattr(surface, "visibility_km", None)
    cb_score = int(getattr(surface, "cb_score", 0) or 0)

    if precip_rate is not None and precip_rate >= 0.2:
        score = max(score, 1)
    if precip_rate is not None and precip_rate >= 3.0:
        score = max(score, 2)
    if ceiling is not None and float(ceiling) < 1000.0:
        score = max(score, 2)
    if ceiling is not None and float(ceiling) < 300.0:
        score = max(score, 3)
    if visibility is not None and float(visibility) < 5.0:
        score = max(score, 2)
    if visibility is not None and float(visibility) < 1.0:
        score = max(score, 3)

    # score 2 is potential only. R3 is reserved for a TSRA code produced from
    # strong convection plus precipitation.
    if cb_score >= 2:
        score = max(score, 2)
    if str(getattr(surface, "phenomena", "")) == "TSRA":
        score = 3
    return score


def point_risk_reasons(data: Any, point_index: int) -> tuple[str, ...]:
    point = data.waypoints[point_index]
    surface = point.surface
    reasons: list[str] = []
    phenomena = str(getattr(surface, "phenomena", "—") or "—")
    cb_score = int(getattr(surface, "cb_score", 0) or 0)
    if phenomena == "TSRA":
        reasons.append("модельный TSRA")
    elif cb_score >= 2:
        reasons.append("конвективный потенциал")
    if phenomena not in {"—", "TSRA"}:
        reasons.append(phenomena)

    visibility = getattr(surface, "visibility_km", None)
    ceiling = getattr(surface, "ceiling_m", None)
    precip_rate = _precip_equivalent_rate(surface)
    if visibility is not None and float(visibility) < 5.0:
        reasons.append("видимость <5 км")
    if ceiling is not None and float(ceiling) < 1000.0:
        reasons.append("ВНГО AGL <1000 м")
    if precip_rate is not None and precip_rate >= 3.0:
        reasons.append("сильные осадки")
    elif precip_rate is not None and precip_rate >= 0.2:
        reasons.append("осадки")

    icing = np.asarray(data.icing_score[:, point_index], dtype=float)
    turbulence = np.asarray(data.turbulence_score[:, point_index], dtype=float)
    wind = np.asarray(data.wind_speed_ms[:, point_index], dtype=float)
    max_icing = float(np.nanmax(icing)) if icing.size else 0.0
    max_turbulence = float(np.nanmax(turbulence)) if turbulence.size else 0.0
    finite_wind = wind[np.isfinite(wind)]
    max_wind = float(np.max(finite_wind)) if finite_wind.size else 0.0
    if max_icing >= 2:
        reasons.append("прокси обледенения")
    elif max_icing >= 1:
        reasons.append("слабый icing proxy")
    if max_turbulence >= 2:
        reasons.append("прокси CAT/болтанки")
    elif max_turbulence >= 1:
        reasons.append("сдвиг/CAT proxy")
    if max_wind >= 30.0:
        reasons.append("ветер ≥30 м/с")
    elif max_wind >= 20.0:
        reasons.append("сильный ветер")
    if not reasons:
        reasons.append("значимых модельных рисков нет")
    return tuple(dict.fromkeys(reasons))


def _nearest_row(profile, level_hpa: int):
    if profile.empty:
        return None
    index = (profile["pressure_hpa"] - float(level_hpa)).abs().idxmin()
    row = profile.loc[index]
    return row if abs(float(row["pressure_hpa"]) - float(level_hpa)) <= 30.0 else None


def _diagnostic_arrays(data, levels_hpa: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_levels = len(levels_hpa)
    n_points = len(data.waypoints)
    icing = np.zeros((n_levels, n_points), dtype=int)
    turbulence = np.zeros((n_levels, n_points), dtype=int)
    cloud = np.zeros((n_levels, n_points), dtype=bool)

    for point_index, point in enumerate(data.waypoints):
        profile = point.profile
        if "icing_proxy_score" not in profile or "turbulence_proxy_score" not in profile:
            profile = add_profile_diagnostics(profile)
        for level_index, level in enumerate(levels_hpa):
            row = _nearest_row(profile, level)
            if row is None:
                continue
            icing[level_index, point_index] = int(row.get("icing_proxy_score", 0) or 0)
            turbulence[level_index, point_index] = int(row.get("turbulence_proxy_score", 0) or 0)
            cloud[level_index, point_index] = bool(row.get("cloud_proxy", False))
    return icing, turbulence, cloud


def _surface_flags(data: Any, indices: tuple[int, ...]) -> dict[str, np.ndarray]:
    points = [data.waypoints[index] for index in indices]
    return {
        "thunder": np.asarray([str(point.surface.phenomena) == "TSRA" for point in points], dtype=bool),
        "visibility": np.asarray(
            [
                (point.surface.visibility_km is not None and point.surface.visibility_km < 5.0)
                or (point.surface.ceiling_m is not None and point.surface.ceiling_m < 1000.0)
                for point in points
            ],
            dtype=bool,
        ),
        "precip": np.asarray(
            [(_precip_equivalent_rate(point.surface) or 0.0) >= 0.2 for point in points],
            dtype=bool,
        ),
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import cloudgram_product
    import route_profile as route
    import route_profile_card_policy as card_policy
    import route_profile_contract as contract

    original_contract_builder = contract.build_route_profile_data

    def diagnostic_route_profile(run, lead_hour, lat, lon, progress_callback=None):
        return build_diagnostic_profile(
            run,
            lead_hour,
            lat,
            lon,
            levels_hpa=tuple(route.ROUTE_LEVELS_HPA),
            include_surface_row=False,
            progress_callback=progress_callback,
        )

    def route_surface_cell(run, lead_hour, lat, lon, progress_callback=None, duration_hours=1):
        native_interval = 1 if int(lead_hour) <= 120 else 3
        return cloudgram_product._read_cloudgram_cell(
            run,
            lead_hour,
            lat,
            lon,
            progress_callback=progress_callback,
            duration_hours=native_interval,
        )

    def build_route_profile_data(
        run,
        origin,
        destination,
        departure_lead: int,
        speed_kmh: int = route.ROUTE_DEFAULT_SPEED_KMH,
        mode: str = "simple",
        progress_callback=None,
        max_points: int = contract.ROUTE_MAX_POINTS,
        spatial_step_km: int = int(contract.ROUTE_SPATIAL_STEP_KM),
    ):
        data = original_contract_builder(
            run,
            origin,
            destination,
            departure_lead,
            speed_kmh=speed_kmh,
            mode=mode,
            progress_callback=progress_callback,
            max_points=max_points,
            spatial_step_km=spatial_step_km,
        )
        icing, turbulence, cloud = _diagnostic_arrays(data, tuple(data.levels_hpa))
        enriched = replace(data, icing_score=icing, turbulence_score=turbulence, cloud_mask=cloud)
        return contract.recompute_objective_risk(enriched)

    route.build_profile = diagnostic_route_profile
    route._read_cloudgram_cell = route_surface_cell
    route._shear_severity = shear_severity
    contract.build_route_profile_data = build_route_profile_data
    contract.vertical_risk_for_point = vertical_risk_for_point
    contract.surface_risk = surface_risk
    contract.point_risk_reasons = point_risk_reasons
    card_policy._surface_flags = _surface_flags
    _INSTALLED = True
