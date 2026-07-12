from __future__ import annotations

"""Устойчивый расчёт риска маршрутной точки.

Слабый сдвиг и одиночный шумный слой не должны окрашивать весь маршрут как
«высокий риск». Диагностика остаётся прокси по вертикальному сдвигу GFS и не
подменяет специализированную оценку турбулентности.
"""

from typing import Any

import numpy as np

import route_profile_contract as _contract


def _has_run(values: np.ndarray, threshold: int, minimum_length: int) -> bool:
    mask = np.asarray(values, dtype=float) >= float(threshold)
    run = 0
    for active in mask:
        run = run + 1 if active else 0
        if run >= minimum_length:
            return True
    return False


def shear_severity(shear_ms_per_km: float) -> int:
    """Conservative display/risk bands for the vertical-shear proxy."""

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

    # Icing values are diagnosed independently at each pressure level, so two
    # adjacent severe levels are meaningful. A single shear layer is written to
    # both of its boundary levels by route_profile.py; therefore severe
    # turbulence needs three consecutive nodes (two adjacent shear layers).
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

    # cb_score == 1 is weak instability only; cb_score == 2 is convective
    # potential. Confirmed TSRA remains the only convective R3 condition.
    if cb_score >= 2:
        score = max(score, 2)
    if _contract.confirmed_thunder(surface):
        score = 3
    return score


_contract.vertical_risk_for_point = vertical_risk_for_point
_contract.surface_risk = surface_risk
# The original builder resolves this global at execution time, so the same
# calibrated thresholds feed both the objective risk and the rendered fields.
_contract._route._shear_severity = shear_severity
