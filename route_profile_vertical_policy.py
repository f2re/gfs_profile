from __future__ import annotations

"""Устойчивый расчёт риска маршрутной точки.

Одиночный шумный уровень не должен автоматически окрашивать весь участок как
«высокий риск». Слабая неустойчивость без осадков и явного конвективного сигнала
также не считается самостоятельным риском маршрута.
"""

from typing import Any

import numpy as np

import route_profile_contract as _contract


def _has_adjacent(values: np.ndarray, threshold: int) -> bool:
    mask = np.asarray(values, dtype=float) >= float(threshold)
    return bool(mask.size >= 2 and np.any(mask[:-1] & mask[1:]))


def vertical_risk_for_point(data: Any, point_index: int) -> int:
    icing = np.asarray(data.icing_score[:, point_index], dtype=float)
    turbulence = np.asarray(data.turbulence_score[:, point_index], dtype=float)
    wind_values = np.asarray(data.wind_speed_ms[:, point_index], dtype=float)
    finite_wind = wind_values[np.isfinite(wind_values)]
    max_wind = float(np.max(finite_wind)) if finite_wind.size else 0.0

    severe_persistent = _has_adjacent(icing, 3) or _has_adjacent(turbulence, 3)
    moderate_persistent = _has_adjacent(icing, 2) or _has_adjacent(turbulence, 2)
    max_icing = float(np.nanmax(icing)) if icing.size else 0.0
    max_turbulence = float(np.nanmax(turbulence)) if turbulence.size else 0.0

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

    # cb_score == 1 — только слабая неустойчивость; риска маршрута без иных
    # подтверждений не создаёт. cb_score == 2 — конвективный потенциал.
    if cb_score >= 2:
        score = max(score, 2)
    if _contract.confirmed_thunder(surface):
        score = 3
    return score


_contract.vertical_risk_for_point = vertical_risk_for_point
_contract.surface_risk = surface_risk
