from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AviationColors:
    safe: str = "#2E7D32"
    safe_soft: str = "#E8F5E9"
    caution: str = "#B77900"
    caution_soft: str = "#FFF7D6"
    restricted: str = "#D95F02"
    restricted_soft: str = "#FFF0DF"
    high_risk: str = "#B91C1C"
    high_risk_soft: str = "#FDE8E8"
    cloud: str = "#71879A"
    cloud_soft: str = "#E3E9EE"
    icing: str = "#1976D2"
    icing_soft: str = "#DDEEFF"
    turbulence: str = "#D97706"
    turbulence_soft: str = "#FFF1D6"
    convection: str = "#7C3AED"
    convection_soft: str = "#EEE5FF"
    wind: str = "#173B67"
    wind_soft: str = "#DCE9F7"
    wind_extreme: str = "#0B2342"
    route: str = "#1F5D99"
    route_muted: str = "#73859A"


AVIATION = AviationColors()
RISK_COLORS = (AVIATION.safe, AVIATION.caution, AVIATION.restricted, AVIATION.high_risk)
RISK_SOFT_COLORS = (AVIATION.safe_soft, AVIATION.caution_soft, AVIATION.restricted_soft, AVIATION.high_risk_soft)
RISK_LABELS = ("СПОКОЙНО", "ВНИМАНИЕ", "СЛОЖНО", "ВЫСОКИЙ РИСК")
RISK_SHORT_LABELS = ("спокойно", "внимание", "сложно", "высокий риск")


def risk_color(score: int, *, soft: bool = False) -> str:
    index = max(0, min(3, int(score)))
    return (RISK_SOFT_COLORS if soft else RISK_COLORS)[index]


def risk_label(score: int, *, short: bool = False) -> str:
    index = max(0, min(3, int(score)))
    return (RISK_SHORT_LABELS if short else RISK_LABELS)[index]
