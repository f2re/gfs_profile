from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from PIL import Image

from basemap_cache import local_basemap_overlay
from geocode import GeoPoint
from gfs_core import CACHE_DIR, GfsProfileError, ProgressCallback
from composite_map_io import (
    MAP_BASEMAP_DEFAULT, MAP_MAX_ANIMATION_FRAMES, MAP_RING_STEP_KM,
    _emit, _validate_basemap,
)

WEATHER_CODE_ICONS = {
    "RA": "☔", "SN": "❄", "FZRA": "❄☔", "IP": "◆",
    "RASN": "☔❄", "UP": "◇", "FG": "≋", "TS": "⚡",
    "TSRA": "⚡", "TSSN": "⚡",
}
PRECIP_PHENOMENA = {"RA", "SN", "FZRA", "IP", "RASN", "UP", "TS", "TSRA", "TSSN"}

def weather_code_icon(code: str) -> str:
    return WEATHER_CODE_ICONS.get(str(code or ""), "")

def _masked(values, mask):
    if values is None:
        return None
    return np.ma.masked_where(~mask, values)


def _line_in_view(points: list[tuple[float, float]], radius_km: float) -> bool:
    limit = radius_km * 1.2
    return any(math.hypot(x, y) <= limit for x, y in points)


def _draw_line_collection(
    ax,
    lines: list[list[tuple[float, float]]],
    *,
    color: str,
    linewidth: float,
    alpha: float,
    zorder: float,
    radius_km: float,
) -> None:
    for pts in lines:
        if len(pts) < 2 or not _line_in_view(pts, radius_km):
            continue
        ax.plot(
            [point[0] for point in pts],
            [point[1] for point in pts],
            color=color,
            linewidth=linewidth,
            alpha=alpha,
            zorder=zorder,
        )


def _basemap_status_text(stats: dict) -> str:
    status = stats.get("status")
    if status == "disabled":
        return "Подложка: базовая"
    if status == "missing_cache":
        warnings = stats.get("warnings") or ["локальный кэш не найден"]
        return "Подложка: " + str(warnings[0])
    if status != "ok":
        return "Подложка: локальный кэш не найден"
    warnings = stats.get("warnings") or []
    warning_text = f" · {warnings[0]}" if warnings else ""
    return (
        f"Подложка: Natural Earth {stats.get('resolution') or ''} · "
        f"города {int(stats.get('city_count') or 0)} · "
        f"реки {int(stats.get('river_count') or 0)} · "
        f"водоёмы {int(stats.get('water_count') or 0)} · "
        f"границы {int(stats.get('admin_count') or 0)}"
        f"{warning_text}"
    )


def _draw_legend(fig, data: dict) -> None:
    interval = float(data.get("precip_interval_hours") or 0.0)
    interval_text = f"{interval:g} ч" if interval > 0 else "интервал"
    threshold = float(data.get("phenomenon_rate_threshold_mmh") or 0.1)
    fig.text(
        0.045,
        0.082,
        f"Зелёный: сумма за предыдущие {interval_text}, мм · APCP (резерв PRATE×Δt) · 0.1 0.5 1 3 7 15+",
        fontsize=8.4,
        color="#263238",
    )
    fig.text(
        0.045,
        0.058,
        f"Значок в центре ячейки GFS: явление на срок · PRATE ≥ {threshold:g} мм/ч + CRAIN/CSNOW/CFRZR/CICEP; число под значком = мм/ч",
        fontsize=8.2,
        color="#263238",
    )
    fig.text(
        0.045,
        0.034,
        "☔ дождь · ❄ снег · ❄☔ переохл. дождь · ☔❄ смешанные · ◆ ледяные гранулы · ◇ фаза не определена · ⚡ гроза · ≋ туман",
        fontsize=8.2,
        color="#263238",
    )
    fig.text(
        0.045,
        0.012,
        "Серый: общая облачность, % · тонкая сетка: ячейки GFS 0.25° · стрелки: ветер 500 гПа · VIS подпись в км",
        fontsize=8.2,
        color="#263238",
    )


def _phenomenon_font_size(x: np.ndarray, y: np.ndarray, pixel_size: int) -> float:
    density = max(x.shape) if getattr(x, "ndim", 0) >= 2 else 1
    base = 10.0 if pixel_size >= 1100 else 8.5
    if density <= 10:
        return base
    if density <= 16:
        return base - 1.0
    return max(6.0, base - 2.0)


def _format_rate(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return ""
    numeric = max(0.0, float(value))
    if numeric < 10:
        return f"{numeric:.1f}"
    return f"{numeric:.0f}"


def _draw_phenomena_cells(ax, data: dict, *, pixel_size: int) -> None:
    codes = data.get("phenomenon_code")
    if codes is None:
        return
    x = np.asarray(data["x"])
    y = np.asarray(data["y"])
    mask = np.asarray(data["mask"], dtype=bool)
    code_grid = np.asarray(codes, dtype=object)
    if code_grid.shape != x.shape:
        return
    rate_grid = data.get("precip_rate_mmh")
    if rate_grid is not None:
        rate_grid = np.asarray(rate_grid, dtype=float)
        if rate_grid.shape != x.shape:
            rate_grid = None

    font_size = _phenomenon_font_size(x, y, pixel_size)
    for row, col in np.argwhere(mask):
        code = str(code_grid[row, col] or "")
        icon = weather_code_icon(code)
        if not icon:
            continue
        rate_text = ""
        if code in PRECIP_PHENOMENA and rate_grid is not None:
            rate_text = _format_rate(float(rate_grid[row, col]))
        text = icon if not rate_text else f"{icon}\n{rate_text}"
        edge = "#90a4ae" if code == "FG" else "#90caf9"
        ax.text(
            float(x[row, col]),
            float(y[row, col]),
            text,
            fontsize=font_size,
            color="#263238",
            ha="center",
            va="center",
            linespacing=0.78,
            bbox={
                "boxstyle": "round,pad=0.12",
                "facecolor": "white",
                "edgecolor": edge,
                "linewidth": 0.5,
                "alpha": 0.80,
            },
            zorder=11,
        )


def _draw_visibility_labels(ax, data: dict) -> None:
    visibility = data.get("visibility")
    if visibility is None:
        return
    x = np.asarray(data["x"])
    y = np.asarray(data["y"])
    mask = np.asarray(data["mask"], dtype=bool)
    vis_grid = np.asarray(visibility, dtype=float)
    if vis_grid.shape != x.shape:
        return
    codes = data.get("phenomenon_code")
    code_grid = np.asarray(codes, dtype=object) if codes is not None else None
    rows, cols = x.shape
    step = max(1, int(max(rows, cols) / 8))
    for row in range(0, rows, step):
        for col in range(0, cols, step):
            if not mask[row, col]:
                continue
            vis = float(vis_grid[row, col])
            if not math.isfinite(vis) or vis >= 10.0:
                continue
            # FG is already rendered cell-by-cell as a phenomenon. Keep the numeric
            # visibility label only where it does not collide with another symbol.
            if code_grid is not None and weather_code_icon(str(code_grid[row, col] or "")):
                continue
            text = f"{vis:.1f} км" if vis < 2 else f"{vis:.0f} км"
            ax.text(
                float(x[row, col]),
                float(y[row, col]),
                text,
                fontsize=7.0,
                color="#455a64",
                ha="center",
                va="center",
                bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "#cfd8dc", "alpha": 0.78},
                zorder=10.5,
            )


def write_composite_map_png(
    data: dict,
    path: Path | None = None,
    *,
    pixel_size: int = 1280,
    basemap_overlay: dict | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    _emit(progress_callback, stage="map_plot", message="Строю композитную карту")
    point: GeoPoint = data["point"]
    radius_km = float(data["radius_km"])
    if path is None:
        path = CACHE_DIR / f"map_{data['run'].date}_{data['run'].cycle}_f{data['lead_hour']:03d}_{int(time.time())}.png"
    dpi = 160
    fig_size = pixel_size / dpi
    fig, ax = plt.subplots(figsize=(fig_size, fig_size), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    basemap = _validate_basemap(str(data.get("basemap", MAP_BASEMAP_DEFAULT)))
    basemap_overlay = (
        basemap_overlay
        if basemap_overlay is not None
        else local_basemap_overlay(point.lat, point.lon, radius_km, basemap)
    )
    water = basemap_overlay.get("water_polygons") or []
    rivers = basemap_overlay.get("river_lines") or []
    roads = basemap_overlay.get("road_lines") or []
    admin_lines = basemap_overlay.get("admin_lines") or []
    coastline_lines = basemap_overlay.get("coastline_lines") or []
    cities = basemap_overlay.get("city_points") or []
    overlay_stats = basemap_overlay.get("stats") or {}
    data["overlay_summary"] = overlay_stats
    data["overlay_footer"] = _basemap_status_text(overlay_stats)
    for pts in water:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.fill(xs, ys, facecolor="#dff3fb", edgecolor="#9cc8d8", linewidth=0.5, alpha=0.9, zorder=1)

    x = np.asarray(data["x"])
    y = np.asarray(data["y"])
    mask = np.asarray(data["mask"], dtype=bool)
    cloud = _masked(data.get("cloud"), mask)
    precip = _masked(data.get("precip"), mask)

    if cloud is not None:
        cloud_cmap = ListedColormap(["#ffffff00", "#e9ecef52", "#d0d5da66", "#aeb6bd75", "#858f988a"])
        cloud_norm = BoundaryNorm([0, 20, 40, 60, 80, 101], cloud_cmap.N)
        ax.pcolormesh(x, y, cloud, cmap=cloud_cmap, norm=cloud_norm, shading="auto", zorder=2)
    if precip is not None:
        precip_cmap = ListedColormap(["#ffffff00", "#dff6dd80", "#bceabb99", "#83d184ad", "#48b85ac2", "#189643d6", "#086b32e3"])
        precip_norm = BoundaryNorm([0, 0.1, 0.5, 1, 3, 7, 15, 999], precip_cmap.N)
        ax.pcolormesh(
            x,
            y,
            precip,
            cmap=precip_cmap,
            norm=precip_norm,
            shading="auto",
            edgecolors="#ffffff60",
            linewidth=0.25,
            zorder=3,
        )

    _draw_line_collection(ax, coastline_lines, color="#6b8794", linewidth=0.75, alpha=0.82, zorder=4.1, radius_km=radius_km)
    _draw_line_collection(ax, water, color="#78b8cf", linewidth=0.55, alpha=0.92, zorder=4.2, radius_km=radius_km)
    _draw_line_collection(ax, admin_lines, color="#78909c", linewidth=0.55, alpha=0.55, zorder=4.35, radius_km=radius_km)
    _draw_line_collection(ax, roads, color="#b89f78", linewidth=0.75, alpha=0.72, zorder=4.5, radius_km=radius_km)
    _draw_line_collection(ax, rivers, color="#3f9ec0", linewidth=0.95, alpha=0.94, zorder=5, radius_km=radius_km)

    for ring in np.arange(MAP_RING_STEP_KM, radius_km + 0.1, MAP_RING_STEP_KM):
        circle = plt.Circle((0, 0), ring, fill=False, linewidth=0.8, linestyle="--", edgecolor="#90a4ae", alpha=0.8, zorder=8)
        ax.add_patch(circle)
        ax.text(
            ring / math.sqrt(2),
            ring / math.sqrt(2),
            f"{int(ring)} км",
            fontsize=8,
            color="#607d8b",
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.78},
            zorder=9,
        )

    if data.get("u500") is not None and data.get("v500") is not None:
        step = max(1, int(max(x.shape) / 9))
        uu = np.ma.masked_where(~mask, data["u500"])
        vv = np.ma.masked_where(~mask, data["v500"])
        ax.quiver(
            x[::step, ::step],
            y[::step, ::step],
            uu[::step, ::step],
            vv[::step, ::step],
            color="#263238",
            alpha=0.72,
            scale=360,
            width=0.0032,
            headwidth=3.2,
            zorder=6,
        )

    # Every model cell with an active surface phenomenon is rendered. This is
    # intentionally not stride-sampled and not truncated to an arbitrary count:
    # the symbol belongs to the exact GFS cell whose PRATE/category fields were read.
    _draw_phenomena_cells(ax, data, pixel_size=pixel_size)
    _draw_visibility_labels(ax, data)

    ax.scatter([0], [0], marker="+", s=110, color="#d32f2f", linewidths=2.0, zorder=12)
    ax.text(
        2.5,
        -4.5,
        point.label,
        fontsize=8.5,
        color="#b71c1c",
        fontweight="bold",
        ha="left",
        va="top",
        bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "#ffcdd2", "alpha": 0.9},
        zorder=13,
    )
    for name, cx, cy in cities:
        if abs(cx) <= radius_km * 1.05 and abs(cy) <= radius_km * 1.05:
            ax.scatter([cx], [cy], s=11, color="#37474f", zorder=7.5)
            ax.text(
                cx + 2.0,
                cy + 1.2,
                name,
                fontsize=7.5,
                color="#263238",
                bbox={"boxstyle": "round,pad=0.08", "facecolor": "white", "edgecolor": "none", "alpha": 0.72},
                zorder=7.5,
            )

    limit = radius_km * 1.08
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)
    ax.set_aspect("equal", adjustable="box")
    tick_step = 25 if radius_km <= 60 else 50
    ticks = np.arange(-math.floor(radius_km / tick_step) * tick_step, radius_km + 0.1, tick_step)
    ax.set_xticks(ticks)
    ax.set_yticks(ticks)
    ax.grid(True, color="#eceff1", linewidth=0.7, zorder=0)
    ax.tick_params(labelsize=8, colors="#607d8b")
    ax.set_xlabel("км от центра", fontsize=9, color="#607d8b")
    ax.set_ylabel("км от центра", fontsize=9, color="#607d8b")
    title = f"GFS 0.25 · композитная карта · {point.label} · +{data['lead_hour']} ч"
    subtitle = f"{data['run'].date} {data['run'].cycle}Z · срок {data['valid_time']:%Y-%m-%d %H:%M} UTC · радиус {int(radius_km)} км"
    ax.set_title(title + "\n" + subtitle, fontsize=12, color="#263238", pad=12)
    _draw_legend(fig, data)
    footer = "GFS 0.25 — модель, не радар и не наблюдения."
    overlay_footer = data.get("overlay_footer")
    if overlay_footer:
        footer += " " + str(overlay_footer) + "."
    if data.get("missing"):
        footer += " Нет полей: " + ", ".join(sorted(data["missing"]))
    fig.text(0.045, 0.108, footer, fontsize=7.6, color="#78909c")
    fig.subplots_adjust(left=0.08, right=0.985, top=0.9, bottom=0.17)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    try:
        image = Image.open(path).convert("RGB")
        image.save(path, optimize=True)
    except Exception:
        pass
    _emit(progress_callback, stage="map_plot_done", message="Карта готова")
    return path


def write_composite_map_gif(
    frames: list[dict],
    path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    if not frames:
        raise GfsProfileError("Нет кадров для анимации")
    if len(frames) > MAP_MAX_ANIMATION_FRAMES:
        raise GfsProfileError(f"Для Telegram-анимации допускается не больше {MAP_MAX_ANIMATION_FRAMES} кадров")
    if path is None:
        first = frames[0]
        path = CACHE_DIR / f"map_{first['run'].date}_{first['run'].cycle}_anim_{int(time.time())}.gif"
    basemap = _validate_basemap(str(frames[0].get("basemap", MAP_BASEMAP_DEFAULT)))
    point: GeoPoint = frames[0]["point"]
    basemap_overlay = local_basemap_overlay(point.lat, point.lon, float(frames[0]["radius_km"]), basemap)
    images: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, frame in enumerate(frames, start=1):
            _emit(
                progress_callback,
                stage="map_animation_frame",
                message=f"Строю кадр {index}/{len(frames)}",
                index=index,
                total=len(frames),
                lead_hour=frame["lead_hour"],
            )
            png_path = Path(tmp) / f"frame_{index:03d}.png"
            write_composite_map_png(frame, png_path, pixel_size=960, basemap_overlay=basemap_overlay)
            images.append(Image.open(png_path).convert("P", palette=Image.ADAPTIVE, colors=96))
        images[0].save(path, save_all=True, append_images=images[1:], duration=650, loop=0, optimize=True)
    _emit(progress_callback, stage="map_animation_done", message="Анимация готова")
    return path
