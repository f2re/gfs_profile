"""Offline regional icing maps, using matplotlib and the shared Natural Earth cache.

The spherical Lambert conformal projection is used for northern midlatitudes.
A Mercator limit avoids an ill-conditioned cone for a tiny equatorial region.
Rendering never fetches a basemap or modifies global matplotlib rcParams.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from threading import RLock

import numpy as np

from icing_meteorology import (
    AGREEMENT_LABELS, PRODUCT_TITLES,
)
from icing_presentation import (
    DISPLAY_UNITS, PRODUCT_HINTS, SOURCE_LABEL, display_icing_field, water_profile_missing,
)
from icing_geography import (
    draw_basemap as _basemap, draw_cities as _cities, draw_selected_point,
)

_RENDER_LOCK = RLock()
_BLUE = "#17384c"


def _projector(bbox):
    west, east, south, north = bbox
    lon0, lat0 = np.deg2rad((west + east) / 2), np.deg2rad((south + north) / 2)
    phi1, phi2 = np.deg2rad([south + (north - south) / 4, north - (north - south) / 4])
    if abs(phi2 - phi1) < 1e-9:
        n = np.sin(phi1)
    else:
        n = np.log(np.cos(phi1) / np.cos(phi2)) / np.log(
            np.tan(np.pi / 4 + phi2 / 2) / np.tan(np.pi / 4 + phi1 / 2)
        )
    if abs(n) < 1e-3:
        def project(lon, lat):
            lam, phi = np.deg2rad(lon), np.deg2rad(np.clip(lat, -89.9, 89.9))
            return (lam - lon0) * 6371, (np.log(np.tan(np.pi / 4 + phi / 2)) - np.log(np.tan(np.pi / 4 + lat0 / 2))) * 6371
        return project
    factor = np.cos(phi1) * np.tan(np.pi / 4 + phi1 / 2) ** n / n
    rho0 = 6371 * factor / np.tan(np.pi / 4 + lat0 / 2) ** n

    def project(lon, lat):
        lam, phi = np.deg2rad(lon), np.deg2rad(np.clip(lat, -89.9, 89.9))
        rho = 6371 * factor / np.tan(np.pi / 4 + phi / 2) ** n
        theta = n * (lam - lon0)
        return rho * np.sin(theta), rho0 - rho * np.cos(theta)
    return project


def _boundary(bbox, project):
    west, east, south, north = bbox
    xs, ys = np.linspace(west, east, 120), np.linspace(south, north, 80)
    lon = np.r_[xs, np.full_like(ys, east), xs[::-1], np.full_like(ys, west), west]
    lat = np.r_[np.full_like(xs, south), ys, np.full_like(xs, north), ys[::-1], south]
    return np.column_stack(project(lon, lat))


def _axis_edges(values):
    values = np.asarray(values, dtype=float)
    if len(values) == 1:
        return np.array([values[0] - 0.125, values[0] + 0.125])
    if not (np.all(np.diff(values) > 0) or np.all(np.diff(values) < 0)):
        raise ValueError("Координаты сетки должны быть упорядочены")
    middle = (values[:-1] + values[1:]) / 2
    return np.r_[2 * values[0] - middle[0], middle, 2 * values[-1] - middle[-1]]


def _geographic_grid(ax, bbox, project, clip):
    west, east, south, north = bbox
    lon_step = max(0.25, [0.25, 1, 2, 5, 10, 20, 30][np.searchsorted([1, 5, 10, 25, 50, 120], east - west)])
    lat_step = max(0.25, [0.25, 1, 2, 5, 10, 20][np.searchsorted([1, 5, 10, 25, 60], north - south)])
    for longitude in np.arange(np.ceil(west / lon_step) * lon_step, east + 1e-8, lon_step):
        x, y = project(np.full(150, longitude), np.linspace(south, north, 150))
        line, = ax.plot(x, y, color="#9fb2bf", lw=0.4, ls=":", alpha=0.7, zorder=4)
        line.set_clip_path(clip)
        if west + 0.03 * (east - west) < longitude < east - 0.03 * (east - west):
            lx, ly = project(longitude, south + 0.015 * (north - south))
            suffix = "в.д." if longitude >= 0 else "з.д."
            ax.text(lx, ly, f"{abs(longitude):g}° {suffix}", ha="center", va="bottom", fontsize=8.5, color="#526b79", clip_on=True, zorder=8)
    for latitude in np.arange(np.ceil(south / lat_step) * lat_step, north + 1e-8, lat_step):
        x, y = project(np.linspace(west, east, 150), np.full(150, latitude))
        line, = ax.plot(x, y, color="#9fb2bf", lw=0.4, ls=":", alpha=0.7, zorder=4)
        line.set_clip_path(clip)
        if south + 0.03 * (north - south) < latitude < north - 0.03 * (north - south):
            lx, ly = project(west + 0.012 * (east - west), latitude)
            ax.text(lx, ly, f"{latitude:g}° с.ш.", ha="left", va="center", fontsize=8.5, color="#526b79", clip_on=True, zorder=8)


def _presentation(product):
    if product in {"godske_sum", "godske_max"}:
        return [10, 50, 100, 150, 200, 250, 300, 400, 500], "YlGnBu"
    if product in {"godske_base", "godske_top", "slw_height"}:
        return [-50, 0, 25, 50, 100, 150, 200, 300, 400, 600, 800], "PuBuGn"
    if product in {"slw_level", "slw_max"}:
        return [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 0.8, 1.2], "BuPu"
    if product == "slwp":
        return [1, 5, 10, 25, 50, 100, 200, 400, 800, 1600], "YlGnBu"
    if product == "godske_count":
        return [0.5, 1.5, 2.5, 3.5, 4.5, 5.5], "YlGnBu"
    return [-0.5, 0.5, 1.5, 2.5, 3.5, 4.5], None


def _unknown_mask(fields, product, values, level_hpa):
    unknown = ~np.isfinite(values)
    if product in {"godske_base", "godske_top"}:
        unknown &= ~np.isfinite(fields["godske20_sum_gpm"]) | (fields["godske20_n_layers"] > 0)
    elif product == "slw_height":
        complete = ~water_profile_missing(fields)
        known_dry = np.isfinite(fields["max_slwc_gm3"]) & (fields["max_slwc_gm3"] == 0)
        known_warm = fields["water_valid_above_ground"].any(axis=0) & ~fields["SLWC_has_cold_levels"]
        unknown &= ~((known_dry | known_warm) & complete)
    elif product == "slw_max":
        # A fully available, entirely warm column has no cold-level maximum.
        known_warm = (fields["water_valid_above_ground"].any(axis=0)
                      & ~fields["SLWC_has_cold_levels"] & ~water_profile_missing(fields))
        unknown &= ~known_warm
    return unknown


def render_icing_png(
    fields, lat, lon, product, *, run, lead, bbox, level_hpa=850,
    contour_step=500, output_path, point=None,
) -> Path:
    """Render one regional PNG with explicit physical units and provenance."""
    from matplotlib import colormaps
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch, PathPatch
    from matplotlib.path import Path as MplPath
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    values = display_icing_field(fields, product, level_hpa)
    if lat.shape != values.shape or lon.shape != values.shape or values.ndim != 2:
        raise ValueError("Координаты и поле карты имеют разные размеры")
    if not np.isfinite(lat).all() or not np.isfinite(lon).all():
        raise ValueError("Координаты карты содержат пропуски")
    if contour_step not in (100, 500):
        raise ValueError("Шаг изолиний на карте должен быть 10 или 50 дам")
    if len(bbox) != 4 or not np.isfinite(bbox).all():
        raise ValueError("Некорректный район карты")
    west, east, south, north = map(float, bbox)
    if not (west < east and 0 <= south < north <= 85):
        raise ValueError("Некорректные границы северного района")
    if point is not None:
        try:
            point_lat, point_lon = map(float, point)
        except (ValueError, TypeError) as exc:
            raise ValueError("Пункт задаётся широтой и долготой") from exc
        if not (np.isfinite([point_lat, point_lon]).all() and south <= point_lat <= north and west <= point_lon <= east):
            raise ValueError("Выбранный пункт находится вне района карты")
        point = (point_lat, point_lon)
    if not np.allclose(lat, lat[:, :1], atol=1e-7) or not np.allclose(lon, lon[:1, :], atol=1e-7):
        raise ValueError("Ожидалась регулярная широтно-долготная сетка GFS")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    project = _projector(bbox)
    x, y = project(lon, lat)
    lon_edges = np.clip(_axis_edges(lon[0]), west, east)
    lat_edges = np.clip(_axis_edges(lat[:, 0]), south, north)
    edge_lon, edge_lat = np.meshgrid(lon_edges, lat_edges)
    edge_x, edge_y = project(edge_lon, edge_lat)
    outline = _boundary(bbox, project)
    bounds, palette = _presentation(product)
    categorical = product == "agreement"
    # No diagnosed conditions leave the geographic base visible, including sea
    # and lakes; category zero must not paint the whole map opaque white.
    colors = [(1, 1, 1, 0), "#d7a55d", "#64acb9", "#574895", "#bf7892"] if categorical else colormaps[palette](np.linspace(0.12, 0.95, len(bounds) - 1))
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(bounds, cmap.N)
    height_product = product in {"godske_base", "godske_top", "slw_height"}
    below_display = np.zeros(values.shape, dtype=bool) if height_product else values < bounds[0]
    masked = np.ma.masked_where(~np.isfinite(values) | below_display, values)
    unknown = _unknown_mask(fields, product, values, level_hpa)
    run_dt = run.run_datetime_utc
    valid_dt = run_dt + timedelta(hours=int(lead))
    with _RENDER_LOCK:
        # Size the page around the projected map instead of fitting the map into
        # a fixed, low strip between several blocks of technical text.
        figure_width = 12.8
        map_ratio = float(np.ptp(outline[:, 0]) / np.ptp(outline[:, 1]))
        map_height = min(11.4, max(4.4, (figure_width - 0.28) / map_ratio))
        header_height, footer_height = 1.00, 1.12 if categorical else 1.00
        figure_height = map_height + header_height + footer_height
        fig = Figure(figsize=(figure_width, figure_height), dpi=170, facecolor="white")
        FigureCanvasAgg(fig)
        try:
            ax = fig.add_axes((0.011, footer_height / figure_height, 0.978, map_height / figure_height))
            ax.set_aspect("equal")
            ax.set_facecolor("white")
            border = PathPatch(MplPath(outline), facecolor="#fcfcfa", edgecolor="#9db0bb", linewidth=0.55, zorder=0)
            ax.add_patch(border)
            ax.set_xlim(outline[:, 0].min(), outline[:, 0].max())
            ax.set_ylim(outline[:, 1].min(), outline[:, 1].max())
            ax.set_xticks([]); ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
            mesh = ax.pcolormesh(edge_x, edge_y, masked, shading="flat", cmap=cmap, norm=norm, zorder=2)
            mesh.set_clip_path(border)
            if unknown.any():
                missing = ax.pcolormesh(edge_x, edge_y, np.ma.masked_where(~unknown, np.ones_like(values)), shading="flat", cmap=ListedColormap(["#c9d0d5"]), vmin=0, vmax=1, zorder=3)
                missing.set_clip_path(border)
            has_basemap = _basemap(ax, bbox, project, border)
            _geographic_grid(ax, bbox, project, border)
            if min(values.shape) >= 2 and product in {"godske_sum", "godske_max"}:
                valid_values = values[np.isfinite(values)]
                vmax = min(float(valid_values.max()), 2000) if len(valid_values) else 0
                contour_dam = contour_step / 10.0
                levels = np.arange(contour_dam, vmax + 1e-7, contour_dam)
                if len(levels) and np.ptp(valid_values) > 1e-9:
                    lines = ax.contour(x, y, np.ma.masked_invalid(values), levels=levels, colors="#254b60", linewidths=0.35 if contour_step == 100 else 0.65, alpha=0.45 if contour_step == 100 else 0.8, zorder=6)
                    lines.set_clip_path(border)
                    labels = [v for v in levels if int(v) % 50 == 0]
                    if labels:
                        for label in ax.clabel(lines, levels=labels, fontsize=9, inline=True, fmt="%d"):
                            label.set_clip_path(border); label.set_clip_on(True)
                            label.set_gid("icing_contour")
            if min(values.shape) >= 2 and product == "slw_level":
                k = int(np.flatnonzero(np.isclose(fields["pressure_hpa"], level_hpa))[0])
                temps = np.ma.masked_where(~fields["water_valid_above_ground"][k], fields["temperature_C"][k])
                finite_t = temps.compressed()
                levels = [v for v in (-20, -10, 0) if len(finite_t) and finite_t.min() < v < finite_t.max()]
                if levels:
                    lines = ax.contour(x, y, temps, levels=levels, colors="#4f6b86", linewidths=0.6, zorder=6)
                    lines.set_clip_path(border)
                    for label in ax.clabel(lines, fontsize=9, fmt=lambda v: f"{v:g} °C"):
                        label.set_clip_path(border); label.set_clip_on(True)
                        label.set_gid("icing_contour")
            censored = np.zeros_like(unknown)
            interior_missing = np.zeros_like(unknown)
            if product in {"godske_sum", "godske_max", "godske_count"}:
                active = fields["godske20_sum_gpm"] > 0
                censored = (fields["godske20_lower_open"] | fields["godske20_upper_open"]) & active
            if product.startswith("godske_"):
                interior_missing = fields["godske20_interior_missing"] & ~unknown
            elif product in {"slwp", "slw_max", "slw_height"}:
                interior_missing = water_profile_missing(fields) & ~unknown
            for mask, pattern in ((censored, ".."), (interior_missing, "//")):
                if not mask.any():
                    continue
                if min(values.shape) >= 2:
                    hatch = ax.contourf(x, y, np.where(mask, 1, np.nan), levels=[0.5, 1.5], colors="none", hatches=[pattern], zorder=7)
                else:
                    hatch = ax.pcolor(edge_x, edge_y, np.ma.masked_where(~mask, np.ones_like(values)),
                                      facecolor="none", cmap=ListedColormap([(1, 1, 1, 0)]), hatch=pattern, zorder=7)
                hatch.set_clip_path(border)
                hatch.set_edgecolor("#657783")
                hatch.set_linewidth(0.15)
            legend = []
            if unknown.any():
                missing_label = ("Ниже поверхности / нет данных" if product == "slw_level" else
                                 "Граница не определена" if product in {"godske_base", "godske_top"} else
                                 "Нет данных для расчёта")
                legend.append(Patch(facecolor="#c9d0d5", label=missing_label))
            if censored.any():
                legend.append(Patch(facecolor="none", edgecolor="#657783", hatch="..", label="Слой выходит за доступные уровни"))
            if interior_missing.any():
                missing_text = "Неполные данные по высоте" if product in {"slwp", "slw_max", "slw_height"} else "Пропуски между уровнями"
                legend.append(Patch(facecolor="none", edgecolor="#657783", hatch="//", label=missing_text))
            if legend:
                ax.legend(handles=legend, loc="lower left", fontsize=9.5, framealpha=0.97,
                          edgecolor="#d2dbe0", borderpad=0.5)
            shown = np.isfinite(values) & ~below_display
            if not shown.any():
                if unknown.all():
                    message = "Поле не определено в доступных данных"
                elif product in {"godske_base", "godske_top"} and (fields["godske20_n_layers"] > 0).any():
                    message = "Высота границы вне доступных уровней"
                else:
                    message = "Значений выше порога заливки не выявлено"
                ax.text(0.5, 0.5, message, transform=ax.transAxes, ha="center", va="center", fontsize=12, color=_BLUE, bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "#b4c4ce", "pad": 8}, zorder=20)
            if point is not None:
                draw_selected_point(ax, point, bbox, project, border)
            _cities(ax, bbox, project, border)
            top = lambda inches: 1.0 - inches / figure_height
            bottom = lambda inches: inches / figure_height
            fig.text(0.022, top(0.23), "ОБЛЕДЕНЕНИЕ · GFS 0,25°", fontsize=9.5, weight="bold", color="#44718a")
            fig.text(0.978, top(0.23), f"Исходные данные {run_dt:%d.%m.%Y %H} UTC · +{int(lead)} ч", fontsize=10, color="#526b79", ha="right")
            title = PRODUCT_TITLES[product]
            fig.text(0.022, top(0.58), title, fontsize=19.5 if len(title) < 49 else 17.5, weight="bold", color=_BLUE)
            pressure_range = f"{float(fields['pressure_hpa'][0]):g}–{float(fields['pressure_hpa'][-1]):g} гПа"
            subtitle = f"{level_hpa} гПа · температура ниже 0 °C" if product == "slw_level" else f"{pressure_range}"
            if product.startswith("godske"):
                subtitle = "Метод Годске · " + subtitle
            if product in {"godske_sum", "godske_max"}:
                subtitle += f" · изолинии {contour_step // 10} дам"
            fig.text(0.022, top(0.87), f"Прогноз на {valid_dt:%d.%m.%Y %H:%M} UTC", fontsize=11.4, weight="bold", color=_BLUE)
            fig.text(0.978, top(0.87), subtitle, fontsize=10.4, ha="right", color="#405d6f")
            if categorical:
                patches = [Patch(facecolor=color, edgecolor="#a8b5bf", label=label) for color, label in zip(colors, AGREEMENT_LABELS)]
                fig.legend(handles=patches, loc="lower center", bbox_to_anchor=(0.5, bottom(0.54)), ncol=3, fontsize=9.5, frameon=False)
            else:
                cax = fig.add_axes((0.045, bottom(0.76), 0.84, bottom(0.14)))
                ticks = range(1, 6) if product == "godske_count" else bounds
                colorbar = fig.colorbar(mesh, cax=cax, orientation="horizontal", ticks=ticks, spacing="uniform", extend="both" if height_product else "max")
                colorbar.ax.tick_params(labelsize=9, length=3, pad=3)
                colorbar.ax.set_xticklabels([f"{v:g}".replace(".", ",") for v in ticks])
                fig.text(0.91, bottom(0.80), DISPLAY_UNITS[product], fontsize=11.5, weight="bold", color=_BLUE)
            note = PRODUCT_HINTS[product]
            fig.text(0.022, bottom(0.37), note, fontsize=9.7, color="#405d6f")
            if has_basemap and "ocean" in has_basemap:
                basemap_note = "География: Natural Earth"
            elif has_basemap:
                basemap_note = "Natural Earth · слой морей не установлен"
            else:
                basemap_note = "Подложка не установлена"
            fig.text(0.022, bottom(0.13), f"{SOURCE_LABEL} · NOMADS", fontsize=9.1, color="#526b79")
            fig.text(0.978, bottom(0.13), basemap_note, fontsize=9.1, color="#526b79", ha="right")
            point_metadata = f"; selected point {point[0]:.4f},{point[1]:.4f}" if point is not None else ""
            fig.savefig(target, format="png", dpi=170, metadata={"Title": title, "Description": f"GFS {run_dt.isoformat()} +{int(lead)}h; valid {valid_dt.isoformat()}; {DISPLAY_UNITS[product]}; GFS data; model{point_metadata}"})
        finally:
            fig.clear()
    return target
