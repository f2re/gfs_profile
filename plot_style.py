from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class MeteoColors:
    figure_bg: str = "#F4F7FA"
    axes_bg: str = "#FBFCFE"
    panel_bg: str = "#F8FAFC"
    grid_major: str = "#B7C6D8"
    grid_minor: str = "#D7E0EA"
    axis_text: str = "#1F2A37"
    muted_text: str = "#52616F"
    spine: str = "#8BA0B5"
    temperature: str = "#C83E2D"
    dewpoint: str = "#0B8F70"
    freezing: str = "#2B6CB0"
    minus10: str = "#2C7BE5"
    minus20: str = "#5B6EE1"
    humidity: str = "#2F80ED"
    wind: str = "#2D3748"
    annotation_bg: str = "#FFFFFF"
    annotation_edge: str = "#91A4B7"


METEO = MeteoColors()

WIND_SPEED_BOUNDS_MS = (0, 3, 6, 10, 15, 20, 25, 30, 40, 60)
WIND_SPEED_COLORS = (
    "#F5FBFF",
    "#D5ECFF",
    "#9FD3FF",
    "#5BB6E5",
    "#3DBB89",
    "#B5D94A",
    "#F4C542",
    "#E46A3A",
    "#B83280",
)

TEMPERATURE_BOUNDS_C = (-70, -50, -40, -30, -20, -10, 0, 10, 20, 30, 40)
TEMPERATURE_COLORS = (
    "#442A83",
    "#3555A8",
    "#2D8ACF",
    "#58C3D9",
    "#BDE7E3",
    "#F3F6D0",
    "#F6D36B",
    "#F39B45",
    "#D95335",
    "#9E2F2F",
)

HUMIDITY_BOUNDS_PCT = (0, 20, 40, 60, 80, 90, 100)
HUMIDITY_COLORS = (
    "#F4F1D4",
    "#DDECCB",
    "#B6DDB4",
    "#79C7B0",
    "#3E9BC5",
    "#2364AA",
)

CLOUD_COVER_BOUNDS_PCT = (0, 10, 25, 50, 75, 90, 100)
CLOUD_COVER_COLORS = (
    "#FFFFFF",
    "#EEF3F7",
    "#D8E2EC",
    "#B7C7D7",
    "#879CAF",
    "#5D7388",
)

PRECIP_BOUNDS_MM = (0, 0.1, 0.5, 1, 3, 7, 15, 30)
PRECIP_COLORS = (
    "#FFFFFF",
    "#EAF8E8",
    "#C8EFC2",
    "#90D884",
    "#4FB564",
    "#238B45",
    "#075A2A",
)

CEILING_BOUNDS_M = (0, 200, 500, 1000, 2000, 5000, 12000)
CEILING_COLORS = (
    "#B91C1C",
    "#EA580C",
    "#D6A800",
    "#7CB342",
    "#2E7D32",
    "#1B5E20",
)

CB_BOUNDS = (0, 1, 2, 3, 4)
CB_COLORS = (
    "#EEF2F5",
    "#F6D65B",
    "#F59E0B",
    "#DC2626",
)

PRECIP_TYPE_COLORS = {
    "—": "#F8FAFC",
    "R": "#EAF8E8",
    "S": "#E6F4FF",
    "FZ": "#FDE2E2",
    "IP": "#F3E8FF",
}


def apply_meteo_rcparams(plt) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "figure.facecolor": METEO.figure_bg,
            "axes.facecolor": METEO.axes_bg,
            "axes.edgecolor": METEO.spine,
            "axes.labelcolor": METEO.axis_text,
            "axes.titlecolor": METEO.axis_text,
            "xtick.color": METEO.axis_text,
            "ytick.color": METEO.axis_text,
            "grid.color": METEO.grid_major,
            "grid.linewidth": 0.55,
            "grid.alpha": 0.72,
            "legend.frameon": True,
            "legend.framealpha": 0.94,
            "legend.facecolor": "#FFFFFF",
            "legend.edgecolor": METEO.annotation_edge,
            "savefig.facecolor": METEO.figure_bg,
        }
    )


def style_axis(axis, *, grid: bool = True) -> None:
    axis.set_facecolor(METEO.axes_bg)
    for spine in axis.spines.values():
        spine.set_color(METEO.spine)
        spine.set_linewidth(0.8)
    axis.tick_params(colors=METEO.axis_text, labelsize=8)
    axis.title.set_fontsize(10)
    axis.title.set_fontweight("bold")
    axis.xaxis.label.set_fontsize(9)
    axis.yaxis.label.set_fontsize(9)
    if grid:
        axis.grid(True, which="major", linewidth=0.55, alpha=0.72, color=METEO.grid_major)
        axis.grid(True, which="minor", linewidth=0.35, alpha=0.55, color=METEO.grid_minor)


def add_footer(fig, text: str, *, y: float = 0.012) -> None:
    fig.text(0.5, y, text, ha="center", va="bottom", fontsize=8, color=METEO.muted_text)


def annotation_box_kwargs() -> dict[str, object]:
    return {
        "boxstyle": "round,pad=0.42,rounding_size=0.12",
        "facecolor": METEO.annotation_bg,
        "alpha": 0.94,
        "edgecolor": METEO.annotation_edge,
        "linewidth": 0.8,
    }


def _listed_cmap_and_norm(name: str, colors: tuple[str, ...], bounds: tuple[int | float, ...]):
    from matplotlib.colors import BoundaryNorm, ListedColormap

    cmap = ListedColormap(colors, name=name)
    cmap.set_bad("#E6EBF1")
    norm = BoundaryNorm(bounds, cmap.N, clip=True)
    return cmap, norm


def wind_speed_cmap_and_norm():
    return _listed_cmap_and_norm("gfs_wind_speed_meteo", WIND_SPEED_COLORS, WIND_SPEED_BOUNDS_MS)


def temperature_cmap_and_norm():
    return _listed_cmap_and_norm("gfs_temperature_meteo", TEMPERATURE_COLORS, TEMPERATURE_BOUNDS_C)


def humidity_cmap_and_norm():
    return _listed_cmap_and_norm("gfs_humidity_meteo", HUMIDITY_COLORS, HUMIDITY_BOUNDS_PCT)


def cloud_cover_cmap_and_norm():
    return _listed_cmap_and_norm("gfs_cloud_cover_meteo", CLOUD_COVER_COLORS, CLOUD_COVER_BOUNDS_PCT)


def precip_cmap_and_norm():
    return _listed_cmap_and_norm("gfs_precip_green_meteo", PRECIP_COLORS, PRECIP_BOUNDS_MM)


def ceiling_cmap_and_norm():
    return _listed_cmap_and_norm("gfs_ceiling_meteo", CEILING_COLORS, CEILING_BOUNDS_M)


def cb_cmap_and_norm():
    return _listed_cmap_and_norm("gfs_cb_risk_meteo", CB_COLORS, CB_BOUNDS)


def value_text_color(value: float, *, param: str) -> str:
    if param == "wind":
        if value < 10:
            return "#102033"
        if value < 20:
            return "#071B22"
        return "#FFFFFF"
    if param == "temp":
        if value <= -40 or value >= 25:
            return "#FFFFFF"
        return "#102033"
    if param == "rh":
        if value >= 80:
            return "#FFFFFF"
        return "#102033"
    if param == "cloud":
        return "#FFFFFF" if value >= 75 else "#102033"
    if param == "precip":
        return "#FFFFFF" if value >= 7 else "#102033"
    if param == "ceiling":
        return "#FFFFFF" if value < 200 or value >= 2000 else "#102033"
    if param == "cb":
        return "#FFFFFF" if value >= 3 else "#102033"
    return "#102033"


def wind_text_color(value: float) -> str:
    return value_text_color(value, param="wind")


def wind_speed_bin_labels(bounds: Iterable[int] = WIND_SPEED_BOUNDS_MS) -> list[str]:
    values = list(bounds)
    labels: list[str] = []
    for left, right in zip(values[:-1], values[1:]):
        labels.append(f"{left}–{right}")
    labels[-1] = f"{values[-2]}+"
    return labels
