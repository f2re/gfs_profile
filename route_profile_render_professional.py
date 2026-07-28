from __future__ import annotations

import numpy as np

from route_profile import RouteProfileData
from route_profile_render_common import ISOTHERM_COLORS, configure_axis, confirmed_thunder, draw_header, draw_mode_footer, draw_professional_cards, output_path, prepare_figure
from route_profile_smoothing import RouteDisplayGrid, build_route_display_grid
from route_profile_visual_style import PALETTE, PRO_STYLE, PRO_TEMPERATURE_COLORS


def _temperature_map():
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    cmap = LinearSegmentedColormap.from_list("route_pro_temperature", PRO_TEMPERATURE_COLORS, N=128)
    cmap.set_bad(PALETTE.axes_bg)
    return cmap, Normalize(vmin=-40.0, vmax=25.0, clip=True)


def _draw_temperature(ax, grid: RouteDisplayGrid):
    cmap, norm = _temperature_map()
    return ax.pcolormesh(grid.x_km, grid.pressure_hpa, grid.temperature_c, cmap=cmap, norm=norm, shading="auto", alpha=PRO_STYLE.temperature_alpha, zorder=0)


def _set_hatch_color(contour_set, color: str) -> None:
    for collection in getattr(contour_set, "collections", ()):
        collection.set_edgecolor(color); collection.set_linewidth(0.35)


def _draw_clouds(ax, grid: RouteDisplayGrid) -> None:
    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    available = [level for level in (0.42, 0.70) if float(np.nanmax(grid.cloud_intensity)) >= level]
    if not available:
        return
    contour = ax.contour(xx, yy, grid.cloud_intensity, levels=available, colors=[PALETTE.cloud_mid, PALETTE.cloud][: len(available)], linewidths=[0.55, 0.75][: len(available)], linestyles=["--", "-"][: len(available)], alpha=0.72, zorder=2)
    labels = {0.42: "CLD", 0.70: "CLD dense"}
    ax.clabel(contour, fmt={level: labels[level] for level in available}, fontsize=5.6, inline=True)


def _draw_hazards(ax, grid: RouteDisplayGrid) -> None:
    from matplotlib import rc_context

    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    icing = np.ma.masked_where(grid.icing_intensity < 0.32, grid.icing_intensity)
    turbulence = np.ma.masked_where(grid.turbulence_intensity < 0.32, grid.turbulence_intensity)
    if icing.count():
        with rc_context({"hatch.color": PALETTE.icing, "hatch.linewidth": 0.38}):
            filled = ax.contourf(xx, yy, icing, levels=[0.32, 0.58, 1.01], colors=[PALETTE.icing_light, PALETTE.icing_light], alpha=PRO_STYLE.icing_alpha, hatches=["..", "...."], zorder=3)
        _set_hatch_color(filled, PALETTE.icing)
        levels = [value for value in (0.32, 0.58) if float(np.nanmax(grid.icing_intensity)) >= value]
        contour = ax.contour(xx, yy, grid.icing_intensity, levels=levels, colors=[PALETTE.icing] * len(levels), linewidths=[0.65, 0.95][: len(levels)], zorder=5)
        ax.clabel(contour, fmt={0.32: "ICE1", 0.58: "ICE2+"}, fontsize=5.7, inline=True)
    if turbulence.count():
        with rc_context({"hatch.color": PALETTE.turbulence, "hatch.linewidth": 0.38}):
            filled = ax.contourf(xx, yy, turbulence, levels=[0.32, 0.58, 1.01], colors=[PALETTE.turbulence_light, PALETTE.turbulence_light], alpha=PRO_STYLE.turbulence_alpha, hatches=["/", "//"], zorder=3)
        _set_hatch_color(filled, PALETTE.turbulence)
        levels = [value for value in (0.32, 0.58) if float(np.nanmax(grid.turbulence_intensity)) >= value]
        contour = ax.contour(xx, yy, grid.turbulence_intensity, levels=levels, colors=[PALETTE.turbulence] * len(levels), linewidths=[0.65, 0.95][: len(levels)], zorder=5)
        ax.clabel(contour, fmt={0.32: "TURB1", 0.58: "TURB2+"}, fontsize=5.7, inline=True)


def _draw_guides(ax, data: RouteProfileData, grid: RouteDisplayGrid) -> None:
    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    temperature = grid.temperature_c[np.isfinite(grid.temperature_c)]
    if temperature.size:
        for value in (-20.0, -10.0, 0.0):
            if float(np.min(temperature)) <= value <= float(np.max(temperature)):
                contour = ax.contour(xx, yy, grid.temperature_c, levels=[value], colors=[ISOTHERM_COLORS[value]], linewidths=1.25 if value == 0.0 else 0.92, zorder=8)
                ax.clabel(contour, fmt={value: f"{value:.0f}°C"}, fontsize=6.0, inline=True, inline_spacing=10)
    humidity = grid.humidity_pct[np.isfinite(grid.humidity_pct)]
    rh_levels = [value for value in (80.0, 90.0) if humidity.size and float(np.min(humidity)) <= value <= float(np.max(humidity))]
    if rh_levels:
        contour = ax.contour(xx, yy, grid.humidity_pct, levels=rh_levels, colors=[PALETTE.rh] * len(rh_levels), linewidths=0.62, linestyles="dashed", zorder=6)
        ax.clabel(contour, fmt=lambda value: f"RH {value:.0f}%", fontsize=5.6, inline=True)
    wind = grid.wind_speed_ms[np.isfinite(grid.wind_speed_ms)]
    wind_levels = [value for value in (20.0, 30.0, 40.0) if wind.size and float(np.min(wind)) <= value <= float(np.max(wind))]
    if wind_levels:
        contour = ax.contour(xx, yy, grid.wind_speed_ms, levels=wind_levels, colors=[PALETTE.wind] * len(wind_levels), linewidths=[0.85, 1.10, 1.45][: len(wind_levels)], zorder=7)
        ax.clabel(contour, fmt=lambda value: f"V{value:.0f}", fontsize=5.7, inline=True)

    source_x = np.asarray([point.distance_km for point in data.waypoints], dtype=float)
    source_levels = np.asarray(data.levels_hpa, dtype=float)
    point_step = max(1, int(np.ceil(len(source_x) / 32))); level_step = 2
    u = data.u_wind_ms[::level_step, ::point_step]; v = -data.v_wind_ms[::level_step, ::point_step]
    finite = np.isfinite(u) & np.isfinite(v)
    if finite.any():
        ax.barbs(source_x[::point_step], source_levels[::level_step], np.where(finite, u, 0.0), np.where(finite, v, 0.0), length=4.1, linewidth=0.38, color="#24364B", alpha=0.76, zorder=9)


def _draw_surface_tags(ax, data: RouteProfileData) -> None:
    for point in data.waypoints:
        if confirmed_thunder(point):
            text, color = "TSRA", PALETTE.thunder
        elif (point.surface.precip_mm or 0.0) >= 0.2:
            text, color = "RA", PALETTE.precip
        else:
            continue
        ax.text(point.distance_km, 975.0, text, ha="center", va="center", fontsize=5.8, fontweight="bold", color=color, zorder=10, bbox={"boxstyle": "round,pad=0.16", "fc": "white", "ec": color, "lw": 0.7, "alpha": 0.91})


def _draw_legend(ax, temperature_mappable) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyBboxPatch, Patch

    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.002, 0.05), 0.996, 0.90, transform=ax.transAxes, boxstyle="round,pad=0.008,rounding_size=0.02", facecolor=PALETTE.panel_bg, edgecolor=PALETTE.border, linewidth=0.8))
    cax = ax.inset_axes([0.018, 0.28, 0.15, 0.42])
    colorbar = ax.figure.colorbar(temperature_mappable, cax=cax, orientation="horizontal", ticks=[-40, -20, 0, 20])
    colorbar.set_label("T, °C (фон)", fontsize=6.8, labelpad=2); colorbar.ax.tick_params(labelsize=6, pad=1)
    handles = [
        Line2D([0], [0], color=PALETTE.cloud, linewidth=0.8, linestyle="--", label="CLD"),
        Patch(facecolor=PALETTE.icing_light, edgecolor=PALETTE.icing, hatch="..", label="ICE1/2+"),
        Patch(facecolor=PALETTE.turbulence_light, edgecolor=PALETTE.turbulence, hatch="//", label="TURB1/2+"),
        Line2D([0], [0], color=PALETTE.wind, linewidth=1.2, label="V20/30/40 + barbs"),
        Line2D([0], [0], color=PALETTE.rh, linewidth=0.8, linestyle="--", label="RH80/90"),
        Line2D([0], [0], color=PALETTE.isotherm_0, linewidth=1.2, label="T 0/-10/-20°C"),
        Line2D([0], [0], color=PALETTE.thunder, marker="s", markersize=5, linestyle="None", label="TSRA/RA"),
    ]
    ax.legend(handles=handles, loc="center left", bbox_to_anchor=(0.19, 0.50), ncol=4, fontsize=6.9, frameon=False, handlelength=2.8, columnspacing=1.3)


def render_professional_route_profile(data: RouteProfileData):
    grid = build_route_display_grid(data, "pro")
    fig, ax, legend_ax, cards_ax = prepare_figure(data, professional=True)
    image = _draw_temperature(ax, grid)
    _draw_clouds(ax, grid); _draw_hazards(ax, grid); _draw_guides(ax, data, grid); _draw_surface_tags(ax, data)
    configure_axis(ax, data, grid, professional=True)
    ax.set_title("Профессиональный вертикальный разрез: T / RH / V / ICE / TURB", loc="left", fontsize=10.4, fontweight="bold", pad=8)
    draw_header(fig, data, professional=True); _draw_legend(legend_ax, image); draw_professional_cards(cards_ax, data); draw_mode_footer(fig)
    path = output_path(data)
    try:
        fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=PALETTE.figure_bg)
    except Exception:
        path.unlink(missing_ok=True); raise
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
    return path
