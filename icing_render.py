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
    AGREEMENT_LABELS, PRODUCT_TITLES, PRODUCT_UNITS, select_icing_field,
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
            ax.text(lx, ly, f"{abs(longitude):g}° {suffix}", ha="center", va="bottom", fontsize=7, color="#526b79", clip_on=True, zorder=8)
    for latitude in np.arange(np.ceil(south / lat_step) * lat_step, north + 1e-8, lat_step):
        x, y = project(np.linspace(west, east, 150), np.full(150, latitude))
        line, = ax.plot(x, y, color="#9fb2bf", lw=0.4, ls=":", alpha=0.7, zorder=4)
        line.set_clip_path(clip)
        if south + 0.03 * (north - south) < latitude < north - 0.03 * (north - south):
            lx, ly = project(west + 0.012 * (east - west), latitude)
            ax.text(lx, ly, f"{latitude:g}° с.ш.", ha="left", va="center", fontsize=7, color="#526b79", clip_on=True, zorder=8)


def _basemap(ax, bbox, project, clip):
    from basemap_cache import basemap_data_dir, basemap_resolution, iter_layer_features
    from matplotlib.collections import LineCollection, PolyCollection
    candidates = dict.fromkeys(("50m", "110m", basemap_resolution(), "10m"))
    resolution = next((r for r in candidates if (
        basemap_data_dir() / "natural_earth" / r / f"ne_{r}_coastline.shp"
    ).is_file()), basemap_resolution())
    west, east, south, north = bbox
    query = (south, north, west, east)
    found = False
    for layer, color, width in (("coastline", "#687f8d", 0.45), ("admin0", "#8396a2", 0.3), ("rivers", "#9abfce", 0.25), ("lakes", "#9abfce", 0.25)):
        lines = []
        try:
            for feature in iter_layer_features(layer, query, resolution):
                if len(feature.points) < 2:
                    continue
                points = np.asarray(feature.points, dtype=float)
                lines.append(np.column_stack(project(points[:, 0], points[:, 1])))
        except (OSError, ValueError):
            continue
        if not lines:
            continue
        found |= layer == "coastline"
        if layer == "lakes":
            patches = PolyCollection(lines, facecolors="#ddecf3", edgecolors="none", alpha=0.7, zorder=1)
            ax.add_collection(patches)
            patches.set_clip_path(clip)
        collection = LineCollection(lines, colors=color, linewidths=width, zorder=5)
        ax.add_collection(collection)
        collection.set_clip_path(clip)
    return found


def _cities(ax, bbox, project):
    from matplotlib import patheffects
    west, east, south, north = bbox
    cities = (("Лондон", -0.13, 51.51), ("Берлин", 13.4, 52.52), ("Москва", 37.62, 55.75),
              ("Анкара", 32.86, 39.93), ("Екатеринбург", 60.6, 56.84),
              ("Новосибирск", 82.92, 55.03), ("Красноярск", 92.87, 56.01), ("Мурманск", 33.08, 68.97))
    for name, lon, lat in cities:
        if not (west + 0.02 * (east - west) < lon < east - 0.02 * (east - west) and south < lat < north):
            continue
        x, y = project(lon, lat)
        ax.plot(x, y, "o", color=_BLUE, ms=2, zorder=9)
        label = ax.annotate(name, (x, y), xytext=(3, 4), textcoords="offset points", fontsize=7.5, color=_BLUE, zorder=10)
        label.set_path_effects([patheffects.withStroke(linewidth=2, foreground="white")])


def _presentation(product):
    if product in {"godske_sum", "godske_max"}:
        return [100, 500, 1000, 1500, 2000, 2500, 3000, 4000, 5000], "YlGnBu"
    if product in {"godske_base", "godske_top", "slw_height"}:
        return [-500, 0, 250, 500, 1000, 1500, 2000, 3000, 4000, 6000, 8000], "PuBuGn"
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
        unknown &= ~np.isfinite(fields["max_slwc_gm3"])
    elif product == "slw_max":
        # A fully available, entirely warm column has no cold-level maximum.
        known_warm = fields["water_valid_above_ground"].any(axis=0) & ~fields["SLWC_has_cold_levels"]
        unknown &= ~known_warm
    return unknown


def render_icing_png(
    fields, lat, lon, product, *, run, lead, bbox, level_hpa=850,
    contour_step=500, output_path,
) -> Path:
    """Render one regional PNG with explicit physical units and provenance."""
    from matplotlib import colormaps
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch, PathPatch
    from matplotlib.path import Path as MplPath
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    values = select_icing_field(fields, product, level_hpa)
    if lat.shape != values.shape or lon.shape != values.shape or values.ndim != 2:
        raise ValueError("Координаты и поле карты имеют разные размеры")
    if not np.isfinite(lat).all() or not np.isfinite(lon).all():
        raise ValueError("Координаты карты содержат пропуски")
    if contour_step not in (100, 500):
        raise ValueError("Шаг изолиний должен быть 100 или 500 гп.м")
    if len(bbox) != 4 or not np.isfinite(bbox).all():
        raise ValueError("Некорректный район карты")
    west, east, south, north = map(float, bbox)
    if not (west < east and 0 <= south < north <= 85):
        raise ValueError("Некорректные границы северного района")
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
    colors = ["#f5f7f9", "#d7a55d", "#64acb9", "#574895", "#bf7892"] if categorical else colormaps[palette](np.linspace(0.12, 0.95, len(bounds) - 1))
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(bounds, cmap.N)
    height_product = product in {"godske_base", "godske_top", "slw_height"}
    below_display = np.zeros(values.shape, dtype=bool) if height_product else values < bounds[0]
    masked = np.ma.masked_where(~np.isfinite(values) | below_display, values)
    unknown = _unknown_mask(fields, product, values, level_hpa)
    run_dt = run.run_datetime_utc
    valid_dt = run_dt + timedelta(hours=int(lead))
    with _RENDER_LOCK:
        fig = Figure(figsize=(12.8, 9.8), dpi=170, facecolor="white")
        FigureCanvasAgg(fig)
        try:
            ax = fig.add_axes((0.035, 0.225, 0.93, 0.61))
            ax.set_aspect("equal")
            ax.set_facecolor("#edf2f5")
            border = PathPatch(MplPath(outline), facecolor="#fbfcfd", edgecolor="#8aa0ad", linewidth=0.65, zorder=0)
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
            _cities(ax, bbox, project)
            if min(values.shape) >= 2 and product in {"godske_sum", "godske_max"}:
                valid_values = values[np.isfinite(values)]
                vmax = min(float(valid_values.max()), 20000) if len(valid_values) else 0
                levels = np.arange(contour_step, vmax + 1e-7, contour_step)
                if len(levels) and np.ptp(valid_values) > 1e-9:
                    lines = ax.contour(x, y, np.ma.masked_invalid(values), levels=levels, colors="#254b60", linewidths=0.35 if contour_step == 100 else 0.65, alpha=0.45 if contour_step == 100 else 0.8, zorder=6)
                    lines.set_clip_path(border)
                    labels = [v for v in levels if int(v) % 500 == 0]
                    if labels:
                        ax.clabel(lines, levels=labels, fontsize=7, inline=True, fmt="%d")
            if min(values.shape) >= 2 and product == "slw_level":
                k = int(np.flatnonzero(np.isclose(fields["pressure_hpa"], level_hpa))[0])
                temps = np.ma.masked_where(~fields["water_valid_above_ground"][k], fields["temperature_C"][k])
                finite_t = temps.compressed()
                levels = [v for v in (-20, -10, 0) if len(finite_t) and finite_t.min() < v < finite_t.max()]
                if levels:
                    lines = ax.contour(x, y, temps, levels=levels, colors="#4f6b86", linewidths=0.6, zorder=6)
                    lines.set_clip_path(border)
                    ax.clabel(lines, fontsize=7, fmt=lambda v: f"{v:g}°")
            censored = np.zeros_like(unknown)
            if product in {"godske_sum", "godske_max", "godske_count"}:
                censored = (fields["godske20_lower_open"] | fields["godske20_upper_open"] | fields["godske20_interior_missing"]) & (fields["godske20_sum_gpm"] > 0)
                if censored.any() and min(values.shape) >= 2:
                    hatch = ax.contourf(x, y, np.where(censored, 1, np.nan), levels=[0.5, 1.5], colors="none", hatches=["..."], zorder=7)
                    hatch.set_clip_path(border)
                    hatch.set_edgecolor("#425b6a")
                    hatch.set_linewidth(0.2)
            legend = []
            if unknown.any():
                missing_label = "Уровень под рельефом / нет данных" if product == "slw_level" else "Нет данных / граница не определена"
                legend.append(Patch(facecolor="#c9d0d5", label=missing_label))
            if censored.any():
                legend.append(Patch(facecolor="none", edgecolor="#425b6a", hatch="...", label="Незамкнутый слой / пропуск в профиле"))
            if legend:
                ax.legend(handles=legend, loc="lower left", fontsize=7.5, framealpha=0.95)
            shown = np.isfinite(values) & ~below_display
            if not shown.any():
                if unknown.all():
                    message = "Поле не определено в доступных данных"
                elif product in {"godske_base", "godske_top"} and (fields["godske20_n_layers"] > 0).any():
                    message = "Граница слоя не замкнута доступными уровнями"
                else:
                    message = "Значений выше порога заливки не выявлено"
                ax.text(0.5, 0.5, message, transform=ax.transAxes, ha="center", va="center", fontsize=12, color=_BLUE, bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "#b4c4ce", "pad": 8}, zorder=20)
            fig.text(0.045, 0.956, "ОБЛЕДЕНЕНИЕ · РАСЧЁТНЫЕ ПРОДУКТЫ GFS", fontsize=10.5, weight="bold", color="#44718a")
            fig.text(0.955, 0.956, f"Цикл {run_dt:%d.%m.%Y %H} UTC · +{int(lead)} ч", fontsize=9.5, color="#526b79", ha="right")
            title = PRODUCT_TITLES[product]
            fig.text(0.045, 0.913, title, fontsize=20 if len(title) < 49 else 17.7, weight="bold", color=_BLUE)
            pressure_range = f"{float(fields['pressure_hpa'][0]):g}–{float(fields['pressure_hpa'][-1]):g} гПа"
            subtitle = f"{level_hpa} гПа · T < 0 °C" if product == "slw_level" else f"Доступные уровни {pressure_range}"
            if product.startswith("godske"):
                subtitle = "M8D20: −20 ≤ T < 0 °C; T + 8(T − Td) ≤ 0 · " + subtitle
            fig.text(0.045, 0.878, subtitle, fontsize=11, color="#405d6f")
            if categorical:
                patches = [Patch(facecolor=color, edgecolor="#a8b5bf", label=label) for color, label in zip(colors, AGREEMENT_LABELS)]
                fig.legend(handles=patches, loc="lower center", bbox_to_anchor=(0.5, 0.144), ncol=3, fontsize=8.4, frameon=False)
            else:
                cax = fig.add_axes((0.20, 0.165, 0.63, 0.018))
                ticks = range(1, 6) if product == "godske_count" else bounds
                colorbar = fig.colorbar(mesh, cax=cax, orientation="horizontal", ticks=ticks, spacing="uniform", extend="both" if height_product else "max")
                colorbar.ax.tick_params(labelsize=8)
                colorbar.ax.set_xticklabels([f"{v:g}".replace(".", ",") for v in ticks])
                colorbar.set_label(PRODUCT_UNITS[product], fontsize=9.5, labelpad=4)
            if product in {"godske_sum", "godske_max"}:
                first_note = f"Изолинии: {contour_step} гп.м. Заливка от 100 гп.м. Мощность слоя не является интенсивностью обледенения."
            elif product in {"godske_base", "godske_top"}:
                first_note = "Высоты по HGT: геопотенциальные метры от уровня моря. Незамкнутые границы не выдаются за точные."
            elif product == "slw_height":
                first_note = "Высота ненулевого максимума среди исходных уровней GFS; геопотенциальные метры от уровня моря."
            elif product == "godske_count":
                first_note = "Число отдельных выявленных слоёв M8D20. Пропуски и неполное покрытие отмечены отдельно."
            elif product == "agreement":
                first_note = "SLW: T < 0 °C и водность ≥ 0,001 г/м³. Пересечение проверено в одном высотном интервале."
            else:
                first_note = f"Облачная жидкая вода GFS; дождевая фаза не включена. Заливка от {bounds[0]:g} {PRODUCT_UNITS[product]}.".replace("0.001", "0,001")
            fig.text(0.045, 0.103, first_note, fontsize=9, color="#405d6f")
            if product in {"slw_level", "slw_max", "slw_height"}:
                sampling_note = "По исходным изобарическим уровням; подземные и пропущенные отсчёты исключены."
            else:
                sampling_note = "Интерполяция между уровнями; участки ниже первого надземного уровня и через пропуски не достраиваются."
            fig.text(0.045, 0.078, sampling_note, fontsize=9, color="#405d6f")
            basemap_note = "Natural Earth, офлайн" if has_basemap else "подложка не установлена; координатная сетка"
            fig.text(0.045, 0.046, f"Действителен {valid_dt:%d.%m.%Y %H} UTC · GFS 0,25° · модель, не наблюдение · {basemap_note}", fontsize=9, color="#526b79")
            fig.text(0.045, 0.023, f"Район: {west:g}…{east:g}° долготы, {south:g}…{north:g}° с.ш. · NOAA/NCEP NOMADS", fontsize=8.5, color="#526b79")
            fig.savefig(target, format="png", dpi=170, metadata={"Title": title, "Description": f"GFS {run_dt.isoformat()} +{int(lead)}h; valid {valid_dt.isoformat()}; {PRODUCT_UNITS[product]}; model, not observation"})
        finally:
            fig.clear()
    return target
