from __future__ import annotations


DASH = "—"


def visibility_km(value: float | None) -> float | None:
    if value is None:
        return None
    raw = max(0.0, float(value))
    return raw / 1000.0 if raw > 200.0 else raw


def thunder_score(
    cape: float | None,
    cin: float | None,
    conv_precip_mm: float | None,
    conv_cloud_pct: float | None,
    precip_rate_mmh: float | None,
) -> int:
    """Оценить конвективный/грозовой риск без ложных срабатываний от облачности.

    В прежней реализации обычная общая облачность и слабый CIN независимо
    добавляли баллы. Поэтому сплошная облачность могла дать ``cb_score >= 2`` и
    превратиться в грозу на всём маршруте. Теперь требуется сочетание
    неустойчивости и фактического конвективного сигнала.

    ``conv_cloud_pct`` оставлен в контракте, но сам по себе не является
    доказательством грозы и учитывается только как дополнительный признак при
    уже существующей неустойчивости и конвективных осадках.
    """

    cape_value = max(0.0, float(cape)) if cape is not None else 0.0
    cin_value = float(cin) if cin is not None else None
    conv_precip = max(0.0, float(conv_precip_mm)) if conv_precip_mm is not None else 0.0
    precip_rate = max(0.0, float(precip_rate_mmh)) if precip_rate_mmh is not None else 0.0
    conv_cloud = max(0.0, min(100.0, float(conv_cloud_pct))) if conv_cloud_pct is not None else 0.0

    weak_evidence = conv_precip >= 0.1 or precip_rate >= 0.5
    strong_evidence = conv_precip >= 0.5 or precip_rate >= 1.5
    weak_inhibition = cin_value is None or cin_value > -200.0
    low_inhibition = cin_value is None or cin_value > -100.0

    score = 0

    # Convective precipitation without CAPE metadata is still a signal, but not
    # enough by itself to declare a thunderstorm.
    if conv_precip >= 0.5 or precip_rate >= 1.5:
        score = 1

    if cape_value >= 250.0:
        score = max(score, 1)

    if cape_value >= 500.0 and weak_inhibition and weak_evidence:
        score = max(score, 2)

    if (
        cape_value >= 1000.0
        and low_inhibition
        and strong_evidence
        and (conv_cloud >= 30.0 or conv_precip >= 1.0 or precip_rate >= 3.0)
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


def weather_code(precip_mm: float | None, precip_code: str, storm_score: int, vis_km: float | None) -> str:
    # TSRA requires a strong score. ``storm_score == 2`` is only convective
    # potential and must not be labelled as an observed/explicit thunderstorm.
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
