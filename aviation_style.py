from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AviationColors:
    safe: str = "#2E7D32"
    safe_soft: str = "#DFF3E3"
    caution: str = "#D6A800"
    caution_soft: str = "#FFF2C2"
    restricted: str = "#EA580C"
    restricted_soft: str = "#FFE1CC"
    high_risk: str = "#B91C1C"
    high_risk_soft: str = "#FAD7D7"
    cloud: str = "#8AA4B8"
    cloud_soft: str = "#DCE7EF"
    icing: str = "#16A6C9"
    turbulence: str = "#F59E0B"
    convection: str = "#7C3AED"
    wind: str = "#27364A"
    route: str = "#1F5D99"
    route_muted: str = "#73859A"


AVIATION = AviationColors()
RISK_COLORS = (AVIATION.safe, AVIATION.caution, AVIATION.restricted, AVIATION.high_risk)
RISK_SOFT_COLORS = (AVIATION.safe_soft, AVIATION.caution_soft, AVIATION.restricted_soft, AVIATION.high_risk_soft)
RISK_LABELS = ("МОЖНО ПО МОДЕЛИ", "ОСТОРОЖНО", "С ОГРАНИЧЕНИЯМИ", "НЕ РЕКОМЕНДУЕТСЯ")
RISK_SHORT_LABELS = ("можно", "осторожно", "ограничения", "высокий риск")


def risk_color(score: int, *, soft: bool = False) -> str:
    index = max(0, min(3, int(score)))
    return (RISK_SOFT_COLORS if soft else RISK_COLORS)[index]


def risk_label(score: int, *, short: bool = False) -> str:
    index = max(0, min(3, int(score)))
    return (RISK_SHORT_LABELS if short else RISK_LABELS)[index]
