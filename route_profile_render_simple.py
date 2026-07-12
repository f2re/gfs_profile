from __future__ import annotations

import numpy as np

from route_profile import RouteProfileData
from route_profile_icons import draw_cloud_icon, draw_hazard_icon
from route_profile_render_common import configure_axis, confirmed_thunder, draw_header, draw_mode_footer, draw_simple_cards, finite_max, output_path, prepare_figure
from route_profile_smoothing import RouteDisplayGrid, build_route_display_grid
from route_profile_visual_style import PALETTE, SIMPLE_STYLE, SIMPLE_TEMPERATURE_COLORS


def _temperature_map():
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    cmap = LinearSegmentedColormap.from_list("route_simple_temperature", SIMPLE_TEMPERATURE_COLORS, N=256)
    cmap.set_bad(PALETTE.axes_bg)
    return cmap, Normalize(vmin=-40.0, vmax=30.0, clip=True)


def _components(mask: np.ndarray, minimum: int = 24):
    from scipy.ndimage import label

    labels, count = label(np.asarray(mask, dtype=bool), structure=np.ones((3, 3), dtype=int))
    result = []
    for component in range(1, count + 1):
        rows, cols = np.where(labels == component)
        if rows.size >= minimum:
            result.append((rows, cols))
    return result


def _draw_temperature(ax, grid: RouteDisplayGrid):
    cmap, norm = _temperature_map()
    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    return ax.contourf(xx, yy, grid.temperature_c, levels=np.linspace(-40.0, 30.0, 29), cmap=cmap, norm=norm, alpha=SIMPLE_STYLE.temperature_alpha, antialiased=True, zorder=0)


def _draw_clouds(ax, grid: RouteDisplayGrid) -> None:
    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    cloud = np.ma.masked_where(grid.cloud_intensity < 0.28, grid.cloud_intensity)
    if not cloud.count():
        return
    ax.contourf(xx, yy, cloud, levels=[0.28, 0.48, 0.70, 1.01], colors=["#E4E9EF", "#D4DCE5", "#BEC9D5"], alpha=SIMPLE_STYLE.cloud_alpha, antialiased=True, zorder=2)
    ax.contour(xx, yy, grid.cloud_intensity, levels=[0.42], colors=[PALETTE.cloud], linewidths=0.65, alpha=0.62, zorder=3)
    for rows, cols in _components(grid.cloud_mask, minimum=30)[:9]:
        center_col = int(np.median(cols)); lower_row = int(np.percentile(rows, 72)); upper_row = int(np.percentile(rows, 18))
        draw_cloud_icon(ax, float(grid.x_km[center_col]), float(grid.pressure_hpa[lower_row]), size=0.031, color=PALETTE.cloud_mid, alpha=0.72, zorder=7)
        if abs(lower_row - upper_row) >= 12:
            ax.text(float(grid.x_km[center_col]), float(grid.pressure_hpa[upper_row]), "ОБЛАКА", ha="center", va="center", fontsize=6.6, fontweight="bold", color=PALETTE.cloud, zorder=7)


def _draw_hazard_mass(ax, grid: RouteDisplayGrid, intensity: np.ndarray, *, colors: tuple[str, str, str], edge: str, icon: str, label: str, threshold: float) -> None:
    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    masked = np.ma.masked_where(intensity < threshold, intensity)
    if not masked.count():
        return
    ax.contourf(xx, yy, masked, levels=[threshold, 0.52, 0.72, 1.01], colors=list(colors), alpha=SIMPLE_STYLE.icing_alpha if icon == "icing" else SIMPLE_STYLE.turbulence_alpha, antialiased=True, zorder=4)
    ax.contour(xx, yy, intensity, levels=[threshold], colors=[edge], linewidths=1.05, alpha=0.82, zorder=5)
    for rows, cols in _components(intensity >= threshold, minimum=16)[:8]:
        row = int(np.median(rows)); col = int(np.median(cols)); x = float(grid.x_km[col]); y = float(grid.pressure_hpa[row])
        draw_hazard_icon(ax, icon, x, y, size=0.030, color=edge, alpha=0.95, zorder=9)
        ax.text(x, y + 21.0, label, ha="center", va="center", fontsize=6.6, fontweight="bold", color=edge, zorder=9)


def _draw_wind(ax, grid: RouteDisplayGrid) -> None:
    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    wind = np.ma.masked_where(grid.wind_speed_ms < 18.0, grid.wind_speed_ms)
    if wind.count():
        ax.contourf(xx, yy, wind, levels=[18.0, 22.0, 30.0, 40.0, 80.0], colors=["#EAF2FA", "#D8E7F6", "#C0D8F0", "#9FBDDF"], alpha=SIMPLE_STYLE.wind_alpha, antialiased=True, zorder=3)
        levels = [value for value in (20.0, 30.0, 40.0) if finite_max(grid.wind_speed_ms) >= value]
        if levels:
            contours = ax.contour(xx, yy, grid.wind_speed_ms, levels=levels, colors=[PALETTE.wind] * len(levels), linewidths=[1.15, 1.45, 1.8][: len(levels)], alpha=0.86, zorder=6)
            ax.clabel(contours, fmt=lambda value: f"{value:.0f} м/с", fontsize=6.1, inline=True)
    for col in np.unique(np.linspace(0, grid.x_km.size - 1, 10).round().astype(int)):
        column = grid.wind_speed_ms[:, col]
        valid = np.where(np.isfinite(column) & (column >= 18.0))[0]
        if not valid.size:
            continue
        row = int(valid[np.argmax(column[valid])])
        direction = "wind" if grid.along_track_wind_ms[row, col] >= 0 else "wind_reverse"
        draw_hazard_icon(ax, direction, float(grid.x_km[col]), float(grid.pressure_hpa[row]), size=0.018, color=PALETTE.wind, alpha=0.86, zorder=8)


def _draw_soft_isotherms(ax, grid: RouteDisplayGrid) -> None:
    xx, yy = np.meshgrid(grid.x_km, grid.pressure_hpa)
    finite = grid.temperature_c[np.isfinite(grid.temperature_c)]
    if not finite.size:
        return
    colors = {0.0: PALETTE.isotherm_0, -10.0: PALETTE.isotherm_10, -20.0: PALETTE.isotherm_20}
    for value in (0.0, -10.0, -20.0):
        if float(np.min(finite)) <= value <= float(np.max(finite)):
            contour = ax.contour(xx, yy, grid.temperature_c, levels=[value], colors=[colors[value]], linewidths=0.78 if value == 0.0 else 0.68, alpha=0.58, zorder=6)
            ax.clabel(contour, fmt={value: f"{value:.0f}°C"}, fontsize=5.9, inline=True)


def _draw_surface_weather(ax, data: RouteProfileData) -> None:
    for point in data.waypoints:
        if confirmed_thunder(point):
            draw_hazard_icon(ax, "thunder", point.distance_km, 973.0, size=0.037, color=PALETTE.thunder, zorder=10)
        elif (point.surface.precip_mm or 0.0) >= 0.2:
            draw_hazard_icon(ax, "precip", point.distance_km, 980.0, size=0.026, color=PALETTE.precip, zorder=10)


def _draw_legend(ax, temperature_mappable) -> None:
    from matplotlib.patches import FancyBboxPatch

    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.002, 0.05), 0.996, 0.90, transform=ax.transAxes, boxstyle="round,pad=0.008,rounding_size=0.02", facecolor=PALETTE.panel_bg, edgecolor=PALETTE.border, linewidth=0.8))
    cax = ax.inset_axes([0.018, 0.28, 0.15, 0.42])
    colorbar = ax.figure.colorbar(temperature_mappable, cax=cax, orientation="horizontal", ticks=[-40, -20, 0, 20])
    colorbar.set_label("температура, °C", fontsize=6.8, labelpad=2); colorbar.ax.tick_params(labelsize=6, pad=1)
    items = [("cloud", "облачность", PALETTE.cloud), ("icing", "обледенение", PALETTE.icing), ("turbulence", "болтанка", PALETTE.turbulence), ("wind", "сильный ветер", PALETTE.wind), ("thunder", "гроза", PALETTE.thunder)]
    for index, (key, label, color) in enumerate(items):
        x = 0.22 + index * 0.15
        draw_hazard_icon(ax, key, x, 0.52, size=0.034, transform=ax.transAxes, color=color, zorder=8)
        ax.text(x + 0.030, 0.52, label, transform=ax.transAxes, ha="left", va="center", fontsize=7.0, color=PALETTE.text)


def render_simple_route_profile(data: RouteProfileData):
    grid = build_route_display_grid(data, "simple")
    fig, ax, legend_ax, cards_ax = prepare_figure(data, professional=False)
    image = _draw_temperature(ax, grid)
    _draw_clouds(ax, grid); _draw_wind(ax, grid)
    _draw_hazard_mass(ax, grid, grid.icing_intensity, colors=(PALETTE.icing_light, "#B9DAFA", PALETTE.icing_mid), edge=PALETTE.icing, icon="icing", label="ОБЛЕДЕНЕНИЕ", threshold=0.38)
    _draw_hazard_mass(ax, grid, grid.turbulence_intensity, colors=(PALETTE.turbulence_light, "#FFD9A8", PALETTE.turbulence_mid), edge=PALETTE.turbulence, icon="turbulence", label="БОЛТАНКА", threshold=0.38)
    _draw_soft_isotherms(ax, grid); _draw_surface_weather(ax, data)
    configure_axis(ax, data, grid, professional=False)
    ax.set_title("Сглаженный маршрутный профиль: облачность и зоны риска", loc="left", fontsize=10.4, fontweight="bold", pad=8)
    draw_header(fig, data, professional=False); _draw_legend(legend_ax, image); draw_simple_cards(cards_ax, data); draw_mode_footer(fig)
    path = output_path(data)
    try:
        fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=PALETTE.figure_bg)
    except Exception:
        path.unlink(missing_ok=True); raise
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
    return path
