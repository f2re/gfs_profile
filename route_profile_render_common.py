from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np

from aviation_style import AVIATION
from plot_style import add_footer, apply_meteo_rcparams, style_axis
from route_profile import RouteProfileData
from route_profile_smoothing import RouteDisplayGrid
from route_profile_visual_style import PALETTE
from route_profile_card_policy import draw_simple_cards, draw_professional_cards, route_card_groups

ISOTHERM_COLORS = {
    0.0: PALETTE.isotherm_0,
    -10.0: PALETTE.isotherm_10,
    -20.0: PALETTE.isotherm_20,
}


def finite_max(values: np.ndarray, default: float = 0.0) -> float:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    return float(np.max(array)) if array.size else float(default)


def short_label(value: str, limit: int = 42) -> str:
    text = " ".join(str(value or "точка").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def output_path(data: RouteProfileData) -> Path:
    suffix = (
        f"_{data.mode}_{data.run.date}_{data.run.cycle}"
        f"_f{data.departure_lead:03d}_{data.speed_kmh}kmh.png"
    ).replace("-", "m").replace(" ", "_")
    handle = tempfile.NamedTemporaryFile(
        prefix="gfs_route_profile",
        suffix=suffix,
        delete=False,
    )
    path = Path(handle.name)
    handle.close()
    return path


def confirmed_thunder(point) -> bool:
    return str(getattr(point.surface, "phenomena", "")) == "TSRA"


def configure_axis(
    ax,
    data: RouteProfileData,
    grid: RouteDisplayGrid,
    *,
    professional: bool,
) -> None:
    levels = (1000, 925, 850, 700, 600, 500) if professional else (1000, 850, 700, 500)
    source_levels = np.asarray(data.levels_hpa, dtype=float)
    source_set = set(int(value) for value in source_levels)
    ticks: list[float] = []
    labels: list[str] = []
    for level in levels:
        if level not in source_set:
            continue
        row = int(np.where(source_levels == level)[0][0])
        heights = data.height_m[row, :]
        heights = heights[np.isfinite(heights)]
        height_km = float(np.mean(heights) / 1000.0) if heights.size else math.nan
        ticks.append(float(level))
        labels.append(
            f"{level}\n{height_km:.1f} км"
            if math.isfinite(height_km)
            else str(level)
        )
    ax.set_yticks(ticks)
    ax.set_yticklabels(labels, fontsize=7.4)
    ax.set_ylim(
        float(np.max(grid.pressure_hpa)) + 12.0,
        float(np.min(grid.pressure_hpa)) - 12.0,
    )
    ax.set_xlim(0.0, max(1.0, float(data.total_distance_km)))
    ax.set_ylabel("Давление, гПа / средняя высота")
    count = 11 if professional else 9
    xticks = np.linspace(0.0, float(data.total_distance_km), count)
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{value:.0f} км" for value in xticks], fontsize=7.2)
    style_axis(ax, grid=True)
    ax.grid(
        True,
        color=PALETTE.grid,
        alpha=0.68 if professional else 0.38,
        linewidth=0.52,
    )


def draw_header(fig, data: RouteProfileData, *, professional: bool) -> None:
    mode = "ПРОФЕССИОНАЛЬНЫЙ" if professional else "УПРОЩЁННЫЙ"
    fig.text(
        0.025,
        0.969,
        f"Маршрут: {short_label(data.origin.label)} → "
        f"{short_label(data.destination.label)}",
        ha="left",
        va="top",
        fontsize=17.5,
        fontweight="bold",
        color=PALETTE.text,
    )
    fig.text(
        0.025,
        0.929,
        f"Расстояние: {data.total_distance_km:.0f} км   |   "
        f"скорость: {data.speed_kmh} км/ч   |   "
        f"вылет: +{data.departure_lead} ч   |   "
        f"GFS run: {data.run.date} {data.run.cycle}Z",
        ha="left",
        va="top",
        fontsize=9.0,
        color=PALETTE.text,
    )
    fig.text(
        0.975,
        0.963,
        f"РЕЖИМ: {mode}",
        ha="right",
        va="top",
        fontsize=9.1,
        color="white",
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.50",
            "fc": AVIATION.route,
            "ec": AVIATION.route,
            "alpha": 0.98,
        },
    )


def draw_mode_footer(fig) -> None:
    add_footer(
        fig,
        "GFS data · высоты MSL · оценка участков по исходным точкам",
        y=0.012,
    )


def prepare_figure(data: RouteProfileData, *, professional: bool):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    apply_meteo_rcparams(plt)
    width = max(14.5, min(22.0, 13.5 + len(data.waypoints) * 0.06))
    card_rows = max(1, (len(route_card_groups(data)) + 4) // 5)
    height = (10.3 if professional else 9.3) + 1.5 * (card_rows - 1)
    fig = plt.figure(figsize=(width, height), facecolor=PALETTE.figure_bg)
    grid = fig.add_gridspec(
        3,
        1,
        height_ratios=[5.6, 0.72, (1.85 if professional else 1.65) * card_rows],
        left=0.065,
        right=0.985,
        top=0.855,
        bottom=0.075,
        hspace=0.18,
    )
    return (
        fig,
        fig.add_subplot(grid[0]),
        fig.add_subplot(grid[1]),
        fig.add_subplot(grid[2]),
    )
