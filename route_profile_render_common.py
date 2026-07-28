from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Iterable

import numpy as np

from aviation_style import AVIATION, risk_color, risk_label
from plot_style import add_footer, apply_meteo_rcparams, style_axis
from route_profile import RouteProfileData
from route_profile_icons import draw_hazard_icon
from route_profile_smoothing import RouteDisplayGrid
from route_profile_visual_style import PALETTE
import route_profile_plot as legacy

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


def hazard_tokens(
    data: RouteProfileData,
    indices: Iterable[int],
    limit: int = 4,
):
    selected = tuple(sorted(set(int(index) for index in indices)))
    if not selected:
        return ()
    points = [data.waypoints[index] for index in selected]
    active = {
        "thunder": any(confirmed_thunder(point) for point in points),
        "icing": finite_max(data.icing_score[:, selected]) >= 1,
        "turbulence": finite_max(data.turbulence_score[:, selected]) >= 1,
        "wind": finite_max(data.wind_speed_ms[:, selected]) >= 20.0,
        "visibility": any(
            (
                point.surface.visibility_km is not None
                and point.surface.visibility_km < 5.0
            )
            or (
                point.surface.ceiling_m is not None
                and point.surface.ceiling_m < 1000.0
            )
            for point in points
        ),
        "precip": any((point.surface.precip_mm or 0.0) >= 0.2 for point in points),
        "cloud": bool(np.any(data.cloud_mask[:, selected])),
    }
    result = []
    for key in (
        "thunder",
        "icing",
        "turbulence",
        "wind",
        "visibility",
        "precip",
        "cloud",
    ):
        if active[key] and not (key == "precip" and active["thunder"]):
            result.append(legacy._HAZARD_TOKENS[key])
        if len(result) >= limit:
            break
    return tuple(result)


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
        "GFS grid, не радиозонд. Сглаживание применяется только к отображению; "
        "риск считается по исходным точкам. Не является разрешением на полёт; "
        "обязательны METAR/TAF/SIGMET/GAMET/NOTAM и решение командира.",
        y=0.012,
    )


def groups(data: RouteProfileData):
    return legacy._display_groups(data, max_cards=12)


def _draw_card_shell(ax, group, score: int, *, rounded: bool) -> None:
    from matplotlib.patches import FancyBboxPatch, Rectangle

    width = max(1.0, group.end_km - group.start_km)
    if rounded:
        patch = FancyBboxPatch(
            (group.start_km, 0.04),
            width,
            0.91,
            boxstyle="round,pad=0.01,rounding_size=0.04",
            facecolor=risk_color(score, soft=True),
            edgecolor=risk_color(score),
            linewidth=0.85,
            alpha=0.96,
        )
    else:
        patch = Rectangle(
            (group.start_km, 0.04),
            width,
            0.91,
            facecolor=risk_color(score, soft=True),
            edgecolor="#FFFFFF",
            linewidth=1.0,
        )
    ax.add_patch(patch)


def draw_simple_cards(ax, data: RouteProfileData) -> None:
    ax.set_xlim(0.0, max(1.0, data.total_distance_km))
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")
    ax.set_title(
        "Оценка маршрута по участкам",
        loc="left",
        fontsize=9.4,
        fontweight="bold",
        pad=6,
    )
    for group in groups(data):
        score = int(
            max(data.waypoints[index].risk_score for index in group.point_indices)
        )
        tokens = hazard_tokens(data, group.point_indices, 3)
        point = data.waypoints[group.start_index]
        _draw_card_shell(ax, group, score, rounded=True)
        ax.text(
            group.center_km,
            0.86,
            f"{group.start_km:.0f}–{group.end_km:.0f} км",
            ha="center",
            va="center",
            fontsize=6.3,
            color=PALETTE.muted,
        )
        ax.text(
            group.center_km,
            0.69,
            risk_label(score).replace("ВЫСОКИЙ РИСК", "ВЫСОКИЙ\nРИСК"),
            ha="center",
            va="center",
            fontsize=7.7,
            fontweight="bold",
            color=risk_color(score),
            linespacing=0.9,
        )
        width = max(1.0, group.end_km - group.start_km)
        if tokens:
            for index, token in enumerate(tokens):
                x_value = group.start_km + width * (index + 1) / (len(tokens) + 1)
                draw_hazard_icon(
                    ax,
                    token.key,
                    x_value,
                    0.45,
                    size=0.035,
                    transform=ax.get_xaxis_transform(),
                    color=token.color,
                    zorder=8,
                )
            summary = " · ".join(token.label for token in tokens[:2])
        else:
            ax.text(
                group.center_km,
                0.45,
                "✓",
                ha="center",
                va="center",
                fontsize=12,
                color=AVIATION.safe,
                fontweight="bold",
            )
            summary = "значимых рисков нет"
        ax.text(
            group.center_km,
            0.25,
            summary,
            ha="center",
            va="center",
            fontsize=5.8,
            color=PALETTE.muted,
        )
        ax.text(
            group.center_km,
            0.10,
            f"+{point.lead_hour} ч · {point.valid_time_utc:%d.%m %H}Z",
            ha="center",
            va="center",
            fontsize=5.9,
            color=PALETTE.text,
        )
    ax.text(
        0.5,
        -0.08,
        "Расстояние · срок GFS и UTC-время начала участка",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=8.0,
        color=PALETTE.text,
    )


def draw_professional_cards(ax, data: RouteProfileData) -> None:
    ax.set_xlim(0.0, max(1.0, data.total_distance_km))
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")
    ax.set_title(
        "Профессиональная сводка участков",
        loc="left",
        fontsize=9.4,
        fontweight="bold",
        pad=6,
    )
    for group in groups(data):
        score = int(
            max(data.waypoints[index].risk_score for index in group.point_indices)
        )
        point = data.waypoints[group.start_index]
        _draw_card_shell(ax, group, score, rounded=False)
        ax.text(
            group.center_km,
            0.87,
            f"{group.start_km:.0f}–{group.end_km:.0f} км",
            ha="center",
            va="center",
            fontsize=6.2,
            color=PALETTE.muted,
        )
        ax.text(
            group.center_km,
            0.72,
            f"R{score} · {risk_label(score, short=True)}",
            ha="center",
            va="center",
            fontsize=6.8,
            fontweight="bold",
            color=risk_color(score),
        )
        indices = group.point_indices
        vmax = finite_max(data.wind_speed_ms[:, indices])
        precip = max(
            (data.waypoints[index].surface.precip_mm or 0.0)
            for index in indices
        )
        vis_values = [
            data.waypoints[index].surface.visibility_km
            for index in indices
            if data.waypoints[index].surface.visibility_km is not None
        ]
        ceiling_values = [
            data.waypoints[index].surface.ceiling_m
            for index in indices
            if data.waypoints[index].surface.ceiling_m is not None
        ]
        vis = min(vis_values) if vis_values else None
        ceiling = min(ceiling_values) if ceiling_values else None
        ax.text(
            group.center_km,
            0.43,
            f"Vmax {vmax:.0f} м/с · P {precip:.1f} мм\n"
            f"VIS {'—' if vis is None else f'{vis:.0f} км'} · "
            f"ВНГО {'—' if ceiling is None else f'{ceiling:.0f} м'}",
            ha="center",
            va="center",
            fontsize=5.4,
            color=PALETTE.muted,
            linespacing=1.12,
        )
        ax.text(
            group.center_km,
            0.10,
            f"+{point.lead_hour} ч · {point.valid_time_utc:%d.%m %H}Z",
            ha="center",
            va="center",
            fontsize=5.9,
            color=PALETTE.text,
        )
    ax.text(
        0.5,
        -0.08,
        "Расстояние · срок GFS и UTC-время начала участка",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=8.0,
        color=PALETTE.text,
    )


def prepare_figure(data: RouteProfileData, *, professional: bool):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    apply_meteo_rcparams(plt)
    width = max(14.5, min(22.0, 13.5 + len(data.waypoints) * 0.06))
    height = 10.3 if professional else 9.3
    fig = plt.figure(figsize=(width, height), facecolor=PALETTE.figure_bg)
    grid = fig.add_gridspec(
        3,
        1,
        height_ratios=[5.6, 0.72, 1.85 if professional else 1.65],
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
