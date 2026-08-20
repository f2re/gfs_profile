from __future__ import annotations

import math

DASH = "—"


def visibility_km(value: float | None) -> float | None:
    """Convert the GFS ``VIS`` field from metres to kilometres."""

    if value is None:
        return None
    return max(0.0, float(value)) / 1000.0


def thunder_score(
    cape: float | None,
    cin: float | None,
    conv_precip_mm: float | None,
    conv_cloud_pct: float | None,
    precip_rate_mmh: float | None,
    conv_precip_interval_hours: float = 1.0,
) -> int:
    """Transparent convective-potential score based on consistent GFS layers.

    Accumulated convective precipitation is normalized by its GRIB accumulation
    interval, so the result does not depend on whether a 1 h or 3 h GFS field is
    available. Total cloud and total precipitation rate must not be substituted
    for the convective fields.
    """

    cape_value = max(0.0, float(cape)) if cape is not None else 0.0
    cin_value = float(cin) if cin is not None else None
    conv_amount = max(0.0, float(conv_precip_mm)) if conv_precip_mm is not None else 0.0
    interval = max(1e-6, float(conv_precip_interval_hours or 1.0))
    conv_amount_rate = conv_amount / interval
    conv_rate = max(0.0, float(precip_rate_mmh)) if precip_rate_mmh is not None else 0.0
    conv_cloud = max(0.0, min(100.0, float(conv_cloud_pct))) if conv_cloud_pct is not None else 0.0

    weak_evidence = conv_amount_rate >= 0.1 or conv_rate >= 0.5
    strong_evidence = conv_amount_rate >= 0.5 or conv_rate >= 1.5
    weak_inhibition = cin_value is None or cin_value > -200.0
    low_inhibition = cin_value is None or cin_value > -100.0

    score = 0
    if conv_amount_rate >= 0.5 or conv_rate >= 1.5:
        score = 1
    if cape_value >= 250.0:
        score = max(score, 1)
    if cape_value >= 500.0 and weak_inhibition and weak_evidence:
        score = max(score, 2)
    if (
        cape_value >= 1000.0
        and low_inhibition
        and strong_evidence
        and (conv_cloud >= 30.0 or conv_amount_rate >= 1.0 or conv_rate >= 3.0)
    ):
        score = 3
    return score


def precipitation_code(rain: bool, snow: bool, cold_rain: bool, ice_pellets: bool) -> str:
    parts: list[str] = []
    if rain:
        parts.append("R")
    if snow:
        parts.append("S")
    if cold_rain:
        parts.append("FZ")
    if ice_pellets:
        parts.append("IP")
    return "/".join(parts) if parts else DASH


def _phase_weather_code(precip_code: str) -> str:
    parts = {part for part in str(precip_code or "").split("/") if part and part != DASH}
    if "FZ" in parts:
        return "FZRA"
    if "R" in parts and "S" in parts:
        return "RASN"
    if "IP" in parts:
        return "IP"
    if "S" in parts:
        return "SN"
    if "R" in parts:
        return "RA"
    return "UP"


def instant_weather_code(
    precip_rate_mmh: float | None,
    precip_code: str,
    storm_score: int,
    vis_km: float | None,
    *,
    min_rate_mmh: float = 0.1,
    thunder_min_rate_mmh: float = 0.2,
) -> str:
    """Classify the phenomenon valid at a GFS forecast time.

    Unlike accumulated-precipitation diagnostics, this function never turns an
    APCP amount into a current rain symbol. Active precipitation requires a
    finite precipitation-rate field at the valid time. The categorical GFS
    surface flags determine phase; if rate is present but phase is not, ``UP``
    is returned instead of inventing rain.
    """

    rate = None
    if precip_rate_mmh is not None:
        try:
            candidate = float(precip_rate_mmh)
            if math.isfinite(candidate):
                rate = max(0.0, candidate)
        except (TypeError, ValueError):
            pass

    active = rate is not None and rate >= max(0.0, float(min_rate_mmh))
    if active:
        phase = _phase_weather_code(precip_code)
        if storm_score >= 3 and rate >= max(0.0, float(thunder_min_rate_mmh)):
            if phase in {"RA", "RASN"}:
                return "TSRA"
            if phase == "SN":
                return "TSSN"
            return "TS"
        return phase

    if vis_km is not None:
        try:
            visibility = float(vis_km)
            if math.isfinite(visibility) and visibility < 1.0:
                return "FG"
        except (TypeError, ValueError):
            pass
    return DASH


def weather_code(precip_mm: float | None, precip_code: str, storm_score: int, vis_km: float | None) -> str:
    # Interval products retain their amount-based contract. TSRA is a model
    # diagnosis, not an observation, and must coincide with measurable amount.
    if storm_score >= 3 and precip_mm is not None and precip_mm > 0.2:
        return "TSRA"
    if vis_km is not None and vis_km < 1.0:
        return "FG"
    if precip_mm is None or precip_mm <= 0.05:
        return DASH
    if "FZ" in precip_code:
        return "FZRA"
    if "S" in precip_code and "R" not in precip_code:
        return "SN"
    return "RA"
