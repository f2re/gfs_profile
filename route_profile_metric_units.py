from __future__ import annotations

"""Metric-unit override for the route-profile renderer.

The core route fields are stored in SI units. This module keeps the renderer in
SI as well: route speed is km/h, atmospheric wind is m/s. It patches the single
internal drawing hook before telegram_route imports the public renderer.
"""

import numpy as np

import route_profile_plot as _plot
from aviation_style import AVIATION
from plot_style import METEO


def format_wind_speed_ms(value_ms: float) -> str:
    return f"{float(value_ms):.0f} м/с"


def _draw_hazard_fields_metric(ax, data, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    xx, yy = np.meshgrid(x, levels)

    icing_mask = data.icing_score > 0
    if np.any(icing_mask):
        icing = np.ma.masked_where(~icing_mask, data.icing_score)
        ax.contourf(
            xx,
            yy,
            icing,
            levels=[0.5, 1.5, 2.5, 3.5],
            colors=[AVIATION.icing_soft] * 3,
            alpha=0.62,
            hatches=["..", "...", "...."],
            zorder=4,
        )
        ax.contour(xx, yy, icing_mask.astype(float), levels=[0.5], colors=[AVIATION.icing], linewidths=0.9, zorder=5)

    turbulence_mask = data.turbulence_score > 0
    if np.any(turbulence_mask):
        turbulence = np.ma.masked_where(~turbulence_mask, data.turbulence_score)
        ax.contourf(
            xx,
            yy,
            turbulence,
            levels=[0.5, 1.5, 2.5, 3.5],
            colors=[AVIATION.turbulence_soft] * 3,
            alpha=0.60,
            hatches=["//", "///", "////"],
            zorder=4,
        )
        ax.contour(xx, yy, turbulence_mask.astype(float), levels=[0.5], colors=[AVIATION.turbulence], linewidths=0.9, zorder=5)

    wind = np.ma.masked_invalid(data.wind_speed_ms)
    if wind.count():
        strong = np.ma.masked_where(wind < 20.0, wind)
        if strong.count():
            ax.contourf(
                xx,
                yy,
                strong,
                levels=[20.0, 30.0, 40.0, 80.0],
                colors=[AVIATION.wind_soft, "#B9D2EE", "#86ACD5"],
                alpha=0.36,
                zorder=3,
            )
        min_wind = _plot._finite_min(data.wind_speed_ms)
        max_wind = _plot._finite_max(data.wind_speed_ms)
        contour_levels = [level for level in (20.0, 30.0, 40.0) if min_wind <= level <= max_wind]
        if contour_levels:
            contour = ax.contour(
                xx,
                yy,
                data.wind_speed_ms,
                levels=contour_levels,
                colors=[AVIATION.wind] * len(contour_levels),
                linewidths=[1.15, 1.55, 2.0][: len(contour_levels)],
                zorder=7,
            )
            if professional:
                ax.clabel(contour, fmt=lambda value: f"V{value:.0f} м/с", fontsize=6.0, inline=True, inline_spacing=8)

    if not professional:
        _plot._annotate_zone_runs(ax, x, levels, icing_mask, symbol="❄", label="ЛЁД", color=AVIATION.icing)
        _plot._annotate_zone_runs(ax, x, levels, turbulence_mask, symbol="≈", label="БОЛТ.", color=AVIATION.turbulence)
        strong_point_mask = np.any(data.wind_speed_ms >= 20.0, axis=0)
        for start, end in _plot._mask_runs(strong_point_mask)[:4]:
            center_x = float(np.mean(x[start : end + 1]))
            local = data.wind_speed_ms[:, start : end + 1]
            max_wind_ms = _plot._finite_max(local)
            local_mask = local >= 20.0
            center_y = _plot._zone_pressure_center(local_mask, 0, local_mask.shape[1] - 1, levels, 650.0)
            ax.text(
                center_x,
                center_y,
                f"➤ {format_wind_speed_ms(max_wind_ms)}",
                ha="center",
                va="center",
                fontsize=7.0,
                fontweight="bold",
                color="white",
                zorder=10,
                bbox={"boxstyle": "round,pad=0.22", "fc": AVIATION.wind, "ec": AVIATION.wind_extreme, "lw": 0.8, "alpha": 0.94},
            )


_plot._draw_hazard_fields = _draw_hazard_fields_metric
