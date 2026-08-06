from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.artist import Artist
from matplotlib.transforms import Bbox, blended_transform_factory
from scipy.interpolate import PchipInterpolator

from meteogram_core import MeteogramError, MeteogramSeries, grid_distance_km

COLORS = {
    "text": "#18242d", "muted": "#61717c", "grid": "#cfd8de",
    "temperature": "#d33b2f", "dewpoint": "#16825d", "humidity": "#2477b3",
    "precip": "#1976d2", "probability": "#7555b7", "wind": "#244f8f",
    "gust": "#d46a1f", "pressure": "#5d6870", "night": "#60758b",
    "cloud_low": "#9ebfd3", "cloud_mid": "#7f98b3", "cloud_high": "#b5a9c8",
}
WIND_ARROWS = ("↓", "↙", "←", "↖", "↑", "↗", "→", "↘")


def write_meteogram_png(series: MeteogramSeries, output_path: Path | None = None) -> Path:
    if len(series.times) < 2:
        raise MeteogramError("Для метеограммы требуется не менее двух сроков")
    width = min(17.0, max(11.8, 9.0 + (series.times[-1] - series.times[0]).total_seconds() / 86400 * 0.48))
    dpi = int(os.getenv("METEOGRAM_DPI", "170"))
    figure, axes = plt.subplots(
        5, 1, figsize=(width, 7.0), dpi=dpi, sharex=True,
        gridspec_kw={"height_ratios": (0.85, 1.45, 0.75, 1.10, 1.40), "hspace": 0.10},
    )
    figure.patch.set_facecolor("white")
    x = mdates.date2num(series.times)
    tracked: list[tuple[Artist, int]] = []
    _draw_header(figure, series, tracked)
    _shade_night(axes, series, x)
    _draw_clouds(axes[0], x, series)
    _draw_temperature(axes[1], x, series, tracked)
    _draw_humidity(axes[2], x, series)
    _draw_precipitation(axes[3], x, series, tracked)
    _draw_wind_pressure(axes[4], x, series)
    _finish_axes(figure, axes, series, tracked)
    _resolve_overlaps(figure, tracked)
    if output_path is None:
        handle = tempfile.NamedTemporaryFile(prefix="meteogram_", suffix=".png", delete=False)
        handle.close()
        output_path = Path(handle.name)
    figure.savefig(output_path, dpi=dpi, facecolor="white")
    plt.close(figure)
    return output_path


def _draw_header(figure, series: MeteogramSeries, tracked):
    kind = "Ансамблевая метеограмма" if series.source.ensemble else "Метеограмма"
    members = ""
    if series.source.ensemble:
        members = f" · {series.member_count or 0}/{series.expected_member_count or 0} членов"
    title = figure.text(0.063, 0.975, series.point_label, ha="left", va="top", fontsize=14.5, fontweight="bold", color=COLORS["text"])
    subtitle = figure.text(0.063, 0.942, f"{kind} · {series.source.model}{members}", ha="left", va="top", fontsize=10.1, fontweight="bold", color=COLORS["text"])
    provider = figure.text(0.063, 0.916, f"{series.source.provider} · цикл не передан поставщиком", ha="left", va="top", fontsize=8.1, color=COLORS["muted"])
    period = f"{series.times[0]:%d.%m.%Y %H:%M} — {series.times[-1]:%d.%m.%Y %H:%M} · {series.timezone}"
    period_text = figure.text(0.063, 0.891, period, ha="left", va="top", fontsize=8.2, color=COLORS["text"])
    for artist in (title, subtitle, provider, period_text):
        tracked.append((artist, 100))


def _base_axis(axis, ylabel: str):
    axis.set_axisbelow(True)
    axis.grid(axis="x", color=COLORS["grid"], linewidth=0.55, alpha=0.8)
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines["left"].set_color(COLORS["grid"])
    axis.spines["bottom"].set_color(COLORS["grid"])
    axis.tick_params(labelsize=7.3, length=2.5, colors=COLORS["text"])
    axis.set_ylabel(ylabel, rotation=0, labelpad=18, fontsize=8, color=COLORS["text"])


def _shade_night(axes, series, x):
    is_day = series.values("is_day")
    if not np.isfinite(is_day).any():
        is_day = np.array([0.0 if _solar_elevation(value, series.requested_lat, series.requested_lon) < -0.833 else 1.0 for value in series.times])
    edges = np.empty(len(x) + 1)
    edges[1:-1] = (x[:-1] + x[1:]) / 2
    edges[0] = x[0] - (x[1] - x[0]) / 2
    edges[-1] = x[-1] + (x[-1] - x[-2]) / 2
    start = None
    for index, value in enumerate(is_day <= 0.5):
        if value and start is None:
            start = index
        if start is not None and (not value or index == len(is_day) - 1):
            end = index if not value else index + 1
            for axis in axes:
                axis.axvspan(edges[start], edges[end], color=COLORS["night"], alpha=0.08, linewidth=0, zorder=0)
            start = None


def _solar_elevation(value: datetime, latitude: float, longitude: float) -> float:
    utc = value.astimezone(UTC)
    day = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    gamma = 2 * np.pi / 365 * (day - 1 + (hour - 12) / 24)
    equation = 229.18 * (
        0.000075 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma) - 0.040849 * np.sin(2 * gamma)
    )
    declination = (
        0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma) + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma) + 0.00148 * np.sin(3 * gamma)
    )
    true_solar_minutes = (hour * 60 + equation + 4 * longitude) % 1440
    hour_angle = np.deg2rad(true_solar_minutes / 4 - 180)
    lat = np.deg2rad(latitude)
    cosine = np.sin(lat) * np.sin(declination) + np.cos(lat) * np.cos(declination) * np.cos(hour_angle)
    return float(np.rad2deg(np.arcsin(np.clip(cosine, -1, 1))))


def _draw_clouds(axis, x, series):
    _base_axis(axis, "облака")
    layers = (
        ("cloud_cover_low", 0.0, COLORS["cloud_low"]),
        ("cloud_cover_mid", 1.0, COLORS["cloud_mid"]),
        ("cloud_cover_high", 2.0, COLORS["cloud_high"]),
    )
    if not any(np.isfinite(series.values(name)).any() for name, _, _ in layers):
        cloud = np.clip(series.values("cloud_cover"), 0, 100)
        axis.fill_between(x, 0, cloud / 100 * 3, color=COLORS["cloud_mid"], alpha=0.55, linewidth=0)
        labels = ("", "общая", "")
    else:
        for name, base, color in layers:
            values = np.clip(series.values(name), 0, 100)
            if np.isfinite(values).any():
                sx, sy = _smooth(x, values, 0, 100)
                axis.fill_between(sx, base, base + sy / 100, color=color, alpha=0.60, linewidth=0)
        labels = ("низкие", "средние", "высокие")
    axis.set_ylim(0, 3)
    axis.set_yticks((0.5, 1.5, 2.5), labels=labels)
    axis.axhline(1, color=COLORS["grid"], lw=0.5)
    axis.axhline(2, color=COLORS["grid"], lw=0.5)


def _draw_temperature(axis, x, series, tracked):
    _base_axis(axis, "°C")
    temperature = series.values("temperature_2m")
    if series.source.ensemble:
        q10, q25 = series.statistic("temperature_2m", "q10"), series.statistic("temperature_2m", "q25")
        q75, q90 = series.statistic("temperature_2m", "q75"), series.statistic("temperature_2m", "q90")
        _band(axis, x, q10, q90, COLORS["temperature"], 0.12)
        _band(axis, x, q25, q75, COLORS["temperature"], 0.23)
        _line(axis, x, temperature, COLORS["temperature"], 2.0, "центр")
    else:
        _line(axis, x, temperature, COLORS["temperature"], 2.0, "температура")
        dewpoint = series.values("dew_point_2m")
        if np.isfinite(dewpoint).any():
            _line(axis, x, dewpoint, COLORS["dewpoint"], 1.25, "точка росы", "--")
    axis.axhline(0, color="#7c8991", linewidth=0.75)
    _legend(axis, "upper left", 2)
    finite = np.flatnonzero(np.isfinite(temperature))
    if finite.size:
        minimum = int(finite[np.nanargmin(temperature[finite])])
        _extreme_label(axis, x, temperature, minimum, "min", tracked)
        maximum = int(finite[np.nanargmax(temperature[finite])])
        if maximum != minimum:
            _extreme_label(axis, x, temperature, maximum, "max", tracked)


def _draw_humidity(axis, x, series):
    _base_axis(axis, "%")
    humidity = np.clip(series.values("relative_humidity_2m"), 0, 100)
    if series.source.ensemble:
        _band(axis, x, series.statistic("relative_humidity_2m", "q10"), series.statistic("relative_humidity_2m", "q90"), COLORS["humidity"], 0.12)
        _band(axis, x, series.statistic("relative_humidity_2m", "q25"), series.statistic("relative_humidity_2m", "q75"), COLORS["humidity"], 0.20)
    sx, sy = _smooth(x, humidity, 0, 100)
    axis.fill_between(sx, 0, sy, color=COLORS["humidity"], alpha=0.10)
    axis.plot(sx, sy, color=COLORS["humidity"], lw=1.35)
    axis.set_ylim(0, 100)
    axis.set_yticks((0, 50, 100))


def _draw_precipitation(axis, x, series, tracked):
    _base_axis(axis, "мм/ч")
    values = series.values("precipitation_intensity")
    if series.source.ensemble:
        q50 = series.statistic("precipitation", "q50_intensity")
        values = q50 if np.isfinite(q50).any() else values
    width = max(np.nanmedian(np.diff(x)) * 0.82, 0.015)
    axis.bar(x, np.nan_to_num(values, nan=0.0), width=width, color=COLORS["precip"], alpha=0.72, linewidth=0)
    maximum = max(2.0, float(np.nanmax(values)) * 1.22 if np.isfinite(values).any() else 2.0)
    axis.set_ylim(0, maximum)
    if series.source.ensemble:
        probability = series.values("precipitation_probability_1")
        probability_axis = axis.twinx()
        probability_axis.plot(x, probability, color=COLORS["probability"], lw=1.15, label="P(≥1 мм)")
        probability_axis.set_ylim(0, 100)
        probability_axis.set_ylabel("%", rotation=0, labelpad=12, fontsize=7, color=COLORS["probability"])
        probability_axis.tick_params(labelsize=6.5, colors=COLORS["probability"])
        probability_axis.spines["top"].set_visible(False)
    _daily_totals(axis, x, series, tracked)
    peak_indices = np.flatnonzero(values >= max(2.0, maximum * 0.42))
    for index in peak_indices[:4]:
        label = axis.annotate(f"{values[index]:.1f}", (x[index], values[index]), xytext=(0, 5), textcoords="offset points", ha="center", va="bottom", fontsize=6.2, color=COLORS["text"], bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.88})
        tracked.append((label, 30))


def _daily_totals(axis, x, series, tracked):
    precipitation = series.statistic("precipitation", "q50") if series.source.ensemble else series.values("precipitation")
    dates = [value.date() for value in series.times]
    unique = []
    for date in dates:
        if not unique or unique[-1] != date:
            unique.append(date)
    transform = blended_transform_factory(axis.transData, axis.transAxes)
    for number, date in enumerate(unique):
        indices = [index for index, value in enumerate(dates) if value == date]
        total = float(np.nansum(precipitation[indices]))
        if total < 0.1:
            continue
        artist = axis.text(float(np.mean(x[indices])), 0.94 if number % 2 == 0 else 0.78, f"Σ{total:.1f}", transform=transform, ha="center", va="top", fontsize=6.0, color=COLORS["text"], bbox={"boxstyle": "round,pad=0.10", "fc": "white", "ec": "none", "alpha": 0.78})
        tracked.append((artist, 10))


def _draw_wind_pressure(axis, x, series):
    _base_axis(axis, "м/с")
    wind, gust = series.values("wind_speed_10m"), series.values("wind_gusts_10m")
    if series.source.ensemble:
        _band(axis, x, series.statistic("wind_speed_10m", "q10"), series.statistic("wind_speed_10m", "q90"), COLORS["wind"], 0.12)
    _line(axis, x, wind, COLORS["wind"], 1.55, "ветер")
    if np.isfinite(gust).any():
        _line(axis, x, gust, COLORS["gust"], 1.25, "порывы", "--")
    combined = np.r_[wind, gust]
    upper = max(10.0, float(np.nanmax(combined)) * 1.20 if np.isfinite(combined).any() else 10.0)
    axis.set_ylim(0, upper)
    pressure = series.values("pressure_msl")
    if np.isfinite(pressure).any():
        pressure_axis = axis.twinx()
        pressure_axis.plot(x, pressure, color=COLORS["pressure"], lw=0.95, alpha=0.85)
        low, high = float(np.nanmin(pressure)), float(np.nanmax(pressure))
        margin = max(5.0, (high - low) * 0.30)
        pressure_axis.set_ylim(min(970, low - margin), max(1040, high + margin))
        pressure_axis.set_ylabel("гПа", rotation=0, labelpad=17, fontsize=7, color=COLORS["pressure"])
        pressure_axis.tick_params(labelsize=6.5, colors=COLORS["pressure"])
        pressure_axis.spines["top"].set_visible(False)
    _wind_arrows(axis, x, series.values("wind_direction_10m"))
    _legend(axis, "upper left", 2)


def _wind_arrows(axis, x, direction):
    step = max(1, int(np.ceil(len(x) / 34)))
    transform = blended_transform_factory(axis.transData, axis.transAxes)
    for index in range(0, len(x), step):
        if np.isfinite(direction[index]):
            arrow = WIND_ARROWS[int(((direction[index] + 22.5) % 360) // 45)]
            axis.text(x[index], 0.04, arrow, transform=transform, ha="center", va="bottom", fontsize=8.2, color=COLORS["text"], clip_on=True)


def _finish_axes(figure, axes, series, tracked):
    timezone = series.times[0].tzinfo
    days = (series.times[-1] - series.times[0]).total_seconds() / 86400
    axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=1, tz=timezone))
    weekdays = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    axes[-1].xaxis.set_major_formatter(plt.FuncFormatter(lambda value, _pos: (lambda current: f"{current:%d.%m}\n{weekdays[current.weekday()]}")(mdates.num2date(value, tz=timezone))))
    minor_hours = (6, 12, 18) if days <= 8 else (12,)
    axes[-1].xaxis.set_minor_locator(mdates.HourLocator(byhour=minor_hours, tz=timezone))
    axes[-1].xaxis.set_minor_formatter(mdates.DateFormatter("%H", tz=timezone))
    axes[-1].tick_params(axis="x", which="major", labelsize=6.9, pad=5)
    axes[-1].tick_params(axis="x", which="minor", labelsize=5.8, pad=2)
    axes[-1].set_xlim(series.times[0], series.times[-1])
    for axis in axes[:-1]:
        axis.tick_params(axis="x", labelbottom=False)
    distance = grid_distance_km(series)
    grid = ""
    if series.grid_lat is not None and series.grid_lon is not None:
        grid = f" · расчётная точка {series.grid_lat:.3f},{series.grid_lon:.3f}"
        if distance is not None:
            grid += f" ({distance:.1f} км)"
    generated = datetime.now(UTC)
    footer1 = figure.text(0.063, 0.052, f"Запрошено {series.requested_lat:.4f},{series.requested_lon:.4f}{grid}", ha="left", va="bottom", fontsize=6.5, color=COLORS["muted"])
    footer2 = figure.text(0.063, 0.027, f"Получено {series.retrieved_at_utc:%d.%m.%Y %H:%M} UTC · PNG {generated:%d.%m.%Y %H:%M} UTC · модельный прогноз, не наблюдение", ha="left", va="bottom", fontsize=6.5, color=COLORS["muted"])
    tracked.extend(((footer1, 100), (footer2, 100)))
    figure.subplots_adjust(left=0.078, right=0.94, top=0.855, bottom=0.135)


def _line(axis, x, values, color, width, label, style="-"):
    sx, sy = _smooth(x, values)
    if len(sx):
        axis.plot(sx, sy, color=color, lw=width, label=label, linestyle=style)


def _band(axis, x, lower, upper, color, alpha):
    mask = np.isfinite(lower) & np.isfinite(upper)
    if mask.sum() >= 2:
        axis.fill_between(x, lower, upper, where=mask, interpolate=True, color=color, alpha=alpha, linewidth=0)


def _smooth(x, values, lower=None, upper=None):
    values = np.asarray(values, dtype=float)
    mask = np.isfinite(values)
    if mask.sum() < 2:
        return x[mask], values[mask]
    dense = np.linspace(x[mask][0], x[mask][-1], min(1600, max(120, int(mask.sum() * 4))))
    try:
        smooth = PchipInterpolator(x[mask], values[mask], extrapolate=False)(dense)
    except ValueError:
        dense, smooth = x[mask], values[mask]
    if lower is not None or upper is not None:
        smooth = np.clip(smooth, lower if lower is not None else -np.inf, upper if upper is not None else np.inf)
    return dense, smooth


def _legend(axis, location, columns):
    legend = axis.legend(loc=location, fontsize=6.7, ncol=columns, frameon=True, borderpad=0.25, handlelength=1.7)
    if legend:
        legend.get_frame().set_facecolor("white")
        legend.get_frame().set_edgecolor(COLORS["grid"])
        legend.get_frame().set_alpha(0.92)


def _extreme_label(axis, x, values, index, prefix, tracked):
    offset = (8, 8) if index < len(x) * 0.82 else (-8, 8)
    artist = axis.annotate(f"{prefix} {values[index]:.1f}°", (x[index], values[index]), xytext=offset, textcoords="offset points", ha="left" if offset[0] > 0 else "right", va="bottom", fontsize=6.3, color=COLORS["text"], bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": COLORS["grid"], "lw": 0.4, "alpha": 0.92})
    tracked.append((artist, 40))


def _resolve_overlaps(figure, tracked):
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    kept: list[tuple[Bbox, int]] = []
    for artist, priority in sorted(tracked, key=lambda item: item[1], reverse=True):
        if not artist.get_visible():
            continue
        try:
            bbox = artist.get_window_extent(renderer=renderer).expanded(1.04, 1.12)
        except Exception:
            continue
        if any(bbox.overlaps(other) for other, other_priority in kept if other_priority >= priority):
            artist.set_visible(False)
        else:
            kept.append((bbox, priority))
    figure.canvas.draw()


def audit_meteogram_layout(path: Path) -> dict[str, int]:
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
    return {"width": width, "height": height, "dimension_sum": width + height}
