from __future__ import annotations


DASH = "—"


def visibility_km(value: float | None) -> float | None:
    if value is None:
        return None
    raw = max(0.0, float(value))
    return raw / 1000.0 if raw > 200.0 else raw


def thunder_score(cape: float | None, cin: float | None, conv_precip_mm: float | None, conv_cloud_pct: float | None, precip_rate_mmh: float | None) -> int:
    score = 0
    if cape is not None:
        if cape >= 1000:
            score += 2
        elif cape >= 250:
            score += 1
    if cin is not None and cin > -150:
        score += 1
    if conv_precip_mm is not None and conv_precip_mm >= 0.2:
        score += 1
    if conv_cloud_pct is not None and conv_cloud_pct >= 30:
        score += 1
    if precip_rate_mmh is not None and precip_rate_mmh >= 3.0:
        score += 1
    return max(0, min(3, score))


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


def weather_code(precip_mm: float | None, precip_code: str, storm_score: int, vis_km: float | None) -> str:
    if storm_score >= 2 and precip_mm is not None and precip_mm > 0.2:
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
