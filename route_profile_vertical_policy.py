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

    # score 2 is potential only. R3 is reserved for a TSRA code produced from
    # strong convection plus precipitation.
    if cb_score >= 2:
        score = max(score, 2)
    if str(getattr(surface, "phenomena", "")) == "TSRA":
        score = 3
    return score


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


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import route_profile as route
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

    def build_route_profile_data(*args, **kwargs):
        data = original_contract_builder(*args, **kwargs)
        icing, turbulence, cloud = _diagnostic_arrays(data, tuple(data.levels_hpa))
        enriched = replace(data, icing_score=icing, turbulence_score=turbulence, cloud_mask=cloud)
        return contract.recompute_objective_risk(enriched)

    route.build_profile = diagnostic_route_profile
    route._shear_severity = shear_severity
    contract.build_route_profile_data = build_route_profile_data
    contract.vertical_risk_for_point = vertical_risk_for_point
    contract.surface_risk = surface_risk
    _INSTALLED = True
