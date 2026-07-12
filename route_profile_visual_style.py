from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutePalette:
    figure_bg: str = "#F4F7FB"
    axes_bg: str = "#FCFDFE"
    panel_bg: str = "#F8FAFD"
    text: str = "#152238"
    muted: str = "#56677A"
    grid: str = "#C9D5E2"
    border: str = "#AFC1D4"

    cloud: str = "#75869A"
    cloud_mid: str = "#AEB9C5"
    cloud_light: str = "#E3E8ED"
    icing: str = "#1267C4"
    icing_mid: str = "#78B8F5"
    icing_light: str = "#D8EBFF"
    turbulence: str = "#DC7200"
    turbulence_mid: str = "#F3A348"
    turbulence_light: str = "#FFF0D8"
    wind: str = "#173E70"
    wind_mid: str = "#5E8FC5"
    wind_light: str = "#DCEAF8"
    thunder: str = "#6F2C91"
    thunder_light: str = "#EEE1F6"
    precip: str = "#2F80ED"
    rh: str = "#1B94B1"
    isotherm_0: str = "#D12D2D"
    isotherm_10: str = "#C53A3A"
    isotherm_20: str = "#A92A2A"


@dataclass(frozen=True)
class RouteRenderStyle:
    temperature_alpha: float
    cloud_alpha: float
    icing_alpha: float
    turbulence_alpha: float
    wind_alpha: float
    grid_alpha: float
    antialiased: bool = True


PALETTE = RoutePalette()
SIMPLE_STYLE = RouteRenderStyle(
    temperature_alpha=0.48,
    cloud_alpha=0.56,
    icing_alpha=0.58,
    turbulence_alpha=0.48,
    wind_alpha=0.24,
    grid_alpha=0.42,
)
PRO_STYLE = RouteRenderStyle(
    temperature_alpha=0.16,
    cloud_alpha=0.24,
    icing_alpha=0.30,
    turbulence_alpha=0.25,
    wind_alpha=0.12,
    grid_alpha=0.68,
)

# Colormap order always follows value order: cold -> warm.
SIMPLE_TEMPERATURE_COLORS = (
    "#85AED4",
    "#A9CBE5",
    "#C8E0EF",
    "#DCECF2",
    "#E8F1EE",
    "#F2F4E8",
    "#F7F0DE",
    "#F6E4C5",
    "#F3D6A4",
)
PRO_TEMPERATURE_COLORS = (
    "#E7EDF5",
    "#EDF1F4",
    "#F3F4F1",
    "#F7F4EC",
    "#F4EDE7",
)
