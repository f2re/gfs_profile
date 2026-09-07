from __future__ import annotations

"""WeatherNext 3 surface map rendering and animation."""

import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Polygon
from PIL import Image

from basemap_cache import local_basemap_overlay
from gfs_core import CACHE_DIR
from weathernext3_provider import WeatherNext3Error, WeatherNext3MapFrame

Progress = Callable[[dict[str, Any]], None] | None
PIXEL_SIZE = int(os.getenv("WEATHERNEXT3_MAP_PIXEL_SIZE", "1280"))
FRAME_DURATION_MS = int(os.getenv("WEATHERNEXT3_MAP_FRAME_DURATION_MS", "700"))
FPS = int(os.getenv("WEATHERNEXT3_MAP_FPS", "8"))
PRECIP_VMAX_MM = float(os.getenv("WEATHERNEXT3_PRECIP_VMAX_MM", "30"))

KIND_TITLES = {
    "clouds": "Общая облачность",
    "cloud_layers": "Слои облачности",
    "precip_native": "Осадки · native",
    "precip_imerg": "Осадки · IMERG",
    "precip_experimental": "Осадки · experimental",
    "combo": "Облачность + осадки",
}


def _emit(callback: Progress, stage: str, message: str, **data: Any) -> None:
    if callback:
        callback({"stage": stage, "message": message, **data})


def _lon_delta(lon: float, center_lon: float) -> float:
    return ((float(lon) - float(center_lon) + 180.0) % 360.0) - 180.0


def _xy(lat: float, lon: float, center_lat: float, center_lon: float) -> tuple[float, float]:
    return (
        _lon_delta(lon, center_lon) * 111.195 * max(0.05, math.cos(math.radians(center_lat))),
        (float(lat) - float(center_lat)) * 111.195,
    )


def _grid(frame: WeatherNext3MapFrame, key: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points: list[tuple[float, float, float]] = []
    for row in frame.rows:
        try:
            lat, lon, value = float(row["latitude"]), float(row["longitude"]), float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(item) for item in (lat, lon, value)):
            continue
        x, y = _xy(lat, lon, frame.requested_lat, frame.requested_lon)
        points.append((round(x, 3), round(y, 3), value))
    if not points:
        raise WeatherNext3Error(f"В кадре WeatherNext 3 отсутствует поле {key}")
    xs = np.array(sorted({item[0] for item in points}), dtype=float)
    ys = np.array(sorted({item[1] for item in points}), dtype=float)
    matrix = np.full((len(ys), len(xs)), np.nan, dtype=float)
    xi, yi = {v: i for i, v in enumerate(xs)}, {v: i for i, v in enumerate(ys)}
    for x, y, value in points:
        matrix[yi[y], xi[x]] = value
    return xs, ys, matrix


def _draw_basemap(ax, overlay: dict[str, Any]) -> None:
    for polygon in overlay.get("water_polygons", []):
        if len(polygon) >= 3:
            ax.add_patch(Polygon(polygon, closed=True, facecolor="#dceef8", edgecolor="#9dc5d8", linewidth=0.5, zorder=0))
    for key, width, alpha in (("river_lines", 0.45, 0.65), ("coastline_lines", 0.75, 0.85), ("admin_lines", 0.5, 0.55), ("road_lines", 0.35, 0.38)):
        for line in overlay.get(key, []):
            if len(line) < 2:
                continue
            xy = np.asarray(line, dtype=float)
            ax.plot(xy[:, 0], xy[:, 1], linewidth=width, alpha=alpha, color="#4b5563", zorder=6)
    for name, x, y in overlay.get("city_points", []):
        ax.scatter([x], [y], s=8, color="#111827", zorder=7)
        ax.text(x + 2.0, y + 1.0, str(name), fontsize=6.5, color="#111827", zorder=7)


def _cloud(ax, frame: WeatherNext3MapFrame, key: str = "cloud_total", *, alpha: float = 0.76):
    xs, ys, values = _grid(frame, key)
    return ax.pcolormesh(xs, ys, np.ma.masked_invalid(values), shading="nearest", cmap="Greys", vmin=0.0, vmax=100.0, alpha=alpha, zorder=2)


def _precip(ax, frame: WeatherNext3MapFrame, *, alpha: float = 0.92):
    xs, ys, values = _grid(frame, "precip")
    masked = np.ma.masked_where(~np.isfinite(values) | (values < 0.05), values)
    return ax.pcolormesh(xs, ys, masked, shading="nearest", cmap="turbo", vmin=0.05, vmax=max(1.0, PRECIP_VMAX_MM), alpha=alpha, zorder=4)


def _cloud_layers(ax, frame: WeatherNext3MapFrame):
    total = _cloud(ax, frame, alpha=0.45)
    for key, color, label in (("cloud_low", "#16a34a", "низкая"), ("cloud_mid", "#2563eb", "средняя"), ("cloud_high", "#9333ea", "высокая")):
        xs, ys, values = _grid(frame, key)
        if np.isfinite(values).any() and float(np.nanmax(values)) >= 50.0:
            ax.contour(xs, ys, values, levels=[50, 80], colors=[color, color], linewidths=[0.8, 1.5], alpha=0.9, zorder=5)
        ax.plot([], [], color=color, linewidth=1.5, label=f"{label} ≥50/80%")
    ax.legend(loc="lower left", fontsize=7, framealpha=0.8)
    return total


def write_weathernext3_map_png(frame: WeatherNext3MapFrame, path: str | Path | None = None, *, pixel_size: int = PIXEL_SIZE, basemap: str = "places", basemap_overlay: dict[str, Any] | None = None) -> Path:
    if pixel_size < 640 or pixel_size > 2048:
        raise WeatherNext3Error("WEATHERNEXT3_MAP_PIXEL_SIZE должен быть 640..2048")
    output = Path(path) if path else Path(CACHE_DIR) / "weathernext3" / f"wn3_{frame.kind}_{frame.run.init_time_utc:%Y%m%d%H}_{frame.lead_hour:03d}_{time.time_ns()}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    overlay = basemap_overlay if basemap_overlay is not None else local_basemap_overlay(frame.requested_lat, frame.requested_lon, frame.radius_km, basemap)
    dpi = 128
    fig, ax = plt.subplots(figsize=(pixel_size / dpi, pixel_size / dpi), dpi=dpi)
    ax.set_facecolor("#f8fafc")
    _draw_basemap(ax, overlay)
    if frame.kind == "clouds":
        mesh = _cloud(ax, frame); fig.colorbar(mesh, ax=ax, fraction=0.035, pad=0.02).set_label("Облачность, %")
    elif frame.kind == "cloud_layers":
        mesh = _cloud_layers(ax, frame); fig.colorbar(mesh, ax=ax, fraction=0.035, pad=0.02).set_label("Общая облачность, %")
    elif frame.kind in {"precip_native", "precip_imerg", "precip_experimental"}:
        mesh = _precip(ax, frame); fig.colorbar(mesh, ax=ax, fraction=0.035, pad=0.02).set_label("Осадки за 1 ч, мм")
    elif frame.kind == "combo":
        _cloud(ax, frame, alpha=0.5); mesh = _precip(ax, frame, alpha=0.9); fig.colorbar(mesh, ax=ax, fraction=0.035, pad=0.02).set_label("Осадки за 1 ч, мм")
    else:
        raise WeatherNext3Error(f"Неизвестный вид карты: {frame.kind}")
    radius = float(frame.radius_km)
    for ring in range(50, int(radius) + 1, 50):
        ax.add_patch(Circle((0.0, 0.0), ring, fill=False, linewidth=0.45, alpha=0.3, color="#334155", zorder=8))
    ax.scatter([0], [0], marker="+", s=90, linewidths=1.8, color="#dc2626", zorder=9)
    ax.text(3, 3, frame.point_label[:42], fontsize=8, color="#7f1d1d", zorder=9)
    ax.set(xlim=(-radius, radius), ylim=(-radius, radius), xlabel="км от точки", ylabel="км от точки")
    ax.set_aspect("equal", adjustable="box"); ax.grid(alpha=0.12, linewidth=0.4)
    ax.set_title(f"WeatherNext 3 · {KIND_TITLES.get(frame.kind, frame.kind)}\nrun {frame.run.init_time_utc:%Y-%m-%d %HZ} · +{frame.lead_hour} ч · valid {frame.valid_time_utc:%d.%m %H:%M UTC}", fontsize=11)
    fig.text(0.5, 0.012, "WeatherNext 3 • Google • 64-member statistics • модельный прогноз, не радар и не спутниковое наблюдение", ha="center", va="bottom", fontsize=7, color="#475569")
    fig.tight_layout(rect=(0.01, 0.035, 0.99, 0.98)); fig.savefig(output, dpi=dpi, facecolor="white"); plt.close(fig)
    if not output.exists() or output.stat().st_size < 1024:
        output.unlink(missing_ok=True); raise WeatherNext3Error("PNG-карта WeatherNext 3 не создана")
    return output


def _encode_mp4(paths: list[Path], output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg")
    concat = output.with_suffix(".txt")
    lines: list[str] = []
    duration = max(0.1, FRAME_DURATION_MS / 1000.0)
    for frame in paths:
        escaped = str(frame.resolve()).replace("'", "'\\''")
        lines += [f"file '{escaped}'", f"duration {duration:.3f}"]
    escaped = str(paths[-1].resolve()).replace("'", "'\\''"); lines.append(f"file '{escaped}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-vf", f"fps={max(1, min(30, FPS))},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-an", "-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast", "-crf", "21", "-movflags", "+faststart", str(output)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    finally:
        concat.unlink(missing_ok=True)


def _encode_gif(paths: list[Path], output: Path) -> None:
    images = [Image.open(path).convert("P", palette=Image.Palette.ADAPTIVE) for path in paths]
    try:
        images[0].save(output, save_all=True, append_images=images[1:], duration=max(100, FRAME_DURATION_MS), loop=0, optimize=False)
    finally:
        for image in images: image.close()


def write_weathernext3_animation(frames: list[WeatherNext3MapFrame], path: str | Path | None = None, *, progress_callback: Progress = None, pixel_size: int = PIXEL_SIZE, basemap: str = "places") -> Path:
    if not frames:
        raise WeatherNext3Error("Нет кадров WeatherNext 3 для анимации")
    if len(frames) > 32:
        raise WeatherNext3Error("Анимация WeatherNext 3 ограничена 32 кадрами")
    first = frames[0]
    target = Path(path) if path else Path(CACHE_DIR) / "weathernext3" / f"wn3_{first.kind}_{first.run.init_time_utc:%Y%m%d%H}_{time.time_ns()}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    overlay = local_basemap_overlay(first.requested_lat, first.requested_lon, first.radius_km, basemap)
    with tempfile.TemporaryDirectory(prefix="wn3_map_") as tmp:
        paths: list[Path] = []
        for index, frame in enumerate(frames, start=1):
            _emit(progress_callback, "plot_frame", f"Кадр {index}/{len(frames)}", index=index, total=len(frames), lead_hour=frame.lead_hour)
            frame_path = Path(tmp) / f"frame_{index:03d}.png"
            write_weathernext3_map_png(frame, frame_path, pixel_size=pixel_size, basemap=basemap, basemap_overlay=overlay); paths.append(frame_path)
        _emit(progress_callback, "encode", "Кодирую анимацию", total=len(frames))
        try:
            _encode_mp4(paths, target)
        except (FileNotFoundError, subprocess.CalledProcessError):
            target.unlink(missing_ok=True); target = target.with_suffix(".gif"); _encode_gif(paths, target)
    if not target.exists() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True); raise WeatherNext3Error("Анимация WeatherNext 3 не создана")
    _emit(progress_callback, "done", "Анимация WeatherNext 3 готова", file=str(target), size=target.stat().st_size)
    return target
