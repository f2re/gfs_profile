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
    "#F5FBFF",  # 0-3 calm, almost white-blue
    "#D5ECFF",  # 3-6 weak blue
    "#9FD3FF",  # 6-10 blue
    "#5BB6E5",  # 10-15 cyan-blue
    "#3DBB89",  # 15-20 green
    "#B5D94A",  # 20-25 yellow-green
    "#F4C542",  # 25-30 amber
    "#E46A3A",  # 30-40 orange-red
    "#B83280",  # 40-60 magenta storm-level
)


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


def wind_speed_cmap_and_norm():
    from matplotlib.colors import BoundaryNorm, ListedColormap

    cmap = ListedColormap(WIND_SPEED_COLORS, name="gfs_wind_speed_meteo")
    cmap.set_bad("#E6EBF1")
    norm = BoundaryNorm(WIND_SPEED_BOUNDS_MS, cmap.N, clip=True)
    return cmap, norm


def wind_text_color(value: float) -> str:
    if value < 10:
        return "#102033"
    if value < 20:
        return "#071B22"
    return "#FFFFFF"


def wind_speed_bin_labels(bounds: Iterable[int] = WIND_SPEED_BOUNDS_MS) -> list[str]:
    values = list(bounds)
    labels: list[str] = []
    for left, right in zip(values[:-1], values[1:]):
        labels.append(f"{left}–{right}")
    labels[-1] = f"{values[-2]}+"
    return labels
