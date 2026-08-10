from __future__ import annotations

import math

import numpy as np

from meteogram_core import MeteogramSeries
from meteogram_plot_common import (
    COLORS,
    FOG_CODES,
    _band,
    _base_axis,
    _combined_finite,
    _extreme_label,
    _legend_above,
    _line,
    _outside_zone_label,
    _smooth,
)

def _draw_clouds(axis, x: np.ndarray, series: MeteogramSeries) -> None:
    _base_axis(axis, "")
    axis.set_facecolor("#f4f9fc")
    layers = (
        ("cloud_cover_low", 0.0, COLORS["cloud_low"]),
        ("cloud_cover_mid", 1.0, COLORS["cloud_mid"]),
        ("cloud_cover_high", 2.0, COLORS["cloud_high"]),
    )
    if not any(np.isfinite(series.values(name)).any() for name, _, _ in layers):
        cloud = np.clip(series.values("cloud_cover"), 0, 100)
        sx, sy = _smooth(x, cloud, 0, 100)
        axis.fill_between(
            sx,
            0,
            sy / 100 * 3,
            color=COLORS["cloud_mid"],
            alpha=0.55,
            linewidth=0,
        )
        labels = ("", "общая", "")
    else:
        for name, base, color in layers:
            values = np.clip(series.values(name), 0, 100)
            if series.source.ensemble:
                q10 = np.clip(series.statistic(name, "q10"), 0, 100)
                q90 = np.clip(series.statistic(name, "q90"), 0, 100)
                mask = np.isfinite(q10) & np.isfinite(q90)
                if mask.sum() >= 2:
                    axis.fill_between(
                        x,
                        base + q10 / 100,
                        base + q90 / 100,
                        where=mask,
                        color=color,
                        alpha=0.12,
                        linewidth=0,
                    )
            if np.isfinite(values).any():
                sx, sy = _smooth(x, values, 0, 100)
                axis.fill_between(
                    sx,
                    base,
                    base + sy / 100,
                    color=color,
                    alpha=0.60,
                    linewidth=0,
                )
        labels = ("низкие", "средние", "высокие")
    axis.set_ylim(0, 3)
    axis.set_yticks((0.5, 1.5, 2.5), labels=labels)
    axis.axhline(1, color=COLORS["grid"], lw=0.5)
    axis.axhline(2, color=COLORS["grid"], lw=0.5)
    axis.text(
        0.0,
        1.03,
        "облачность, % · медиана" if series.source.ensemble else "облачность, %",
        transform=axis.transAxes,
        ha="left",
        va="bottom",
        fontsize=7.4,
        fontweight="bold",
        clip_on=False,
    )


def _draw_temperature(axis, x: np.ndarray, series: MeteogramSeries, tracked) -> None:
    _base_axis(axis, "°C")
    temperature = series.values("temperature_2m")
    dewpoint = series.values("dew_point_2m")
    scale_arrays = [temperature, dewpoint]
    if series.source.ensemble:
        scale_arrays.extend(
            [
                series.statistic("temperature_2m", "q10"),
                series.statistic("temperature_2m", "q90"),
                series.statistic("dew_point_2m", "q10"),
                series.statistic("dew_point_2m", "q90"),
            ]
        )
    finite = _combined_finite(scale_arrays)
    lower = (
        min(-20.0, math.floor((float(finite.min()) - 3.0) / 5.0) * 5.0)
        if finite.size
        else -20.0
    )
    upper = (
        max(40.0, math.ceil((float(finite.max()) + 3.0) / 5.0) * 5.0)
        if finite.size
        else 40.0
    )
    _temperature_background(axis, lower, upper)

    if series.source.ensemble:
        q10 = series.statistic("temperature_2m", "q10")
        q25 = series.statistic("temperature_2m", "q25")
        q75 = series.statistic("temperature_2m", "q75")
        q90 = series.statistic("temperature_2m", "q90")
        _band(axis, x, q10, q90, COLORS["temperature"], 0.12)
        _band(axis, x, q25, q75, COLORS["temperature"], 0.23)
        _line(axis, x, temperature, COLORS["temperature"], 2.0, "температура, среднее")
        if np.isfinite(dewpoint).any():
            _band(
                axis,
                x,
                series.statistic("dew_point_2m", "q10"),
                series.statistic("dew_point_2m", "q90"),
                COLORS["dewpoint"],
                0.08,
            )
            _line(
                axis,
                x,
                dewpoint,
                COLORS["dewpoint"],
                1.25,
                "точка росы, среднее",
                "--",
            )
    else:
        _line(
            axis,
            x,
            temperature,
            COLORS["temperature"],
            2.0,
            "температура",
        )
        if np.isfinite(dewpoint).any():
            _line(
                axis,
                x,
                dewpoint,
                COLORS["dewpoint"],
                1.25,
                "точка росы",
                "--",
            )
    axis.set_ylim(lower, upper)
    axis.set_yticks(np.arange(math.ceil(lower / 10) * 10, upper + 0.1, 10))
    _legend_above(axis, columns=3, fontsize=7.0)

    finite_indices = np.flatnonzero(np.isfinite(temperature))
    if finite_indices.size:
        minimum = int(
            finite_indices[np.nanargmin(temperature[finite_indices])]
        )
        maximum = int(
            finite_indices[np.nanargmax(temperature[finite_indices])]
        )
        _extreme_label(axis, x, temperature, minimum, "min", tracked)
        if maximum != minimum:
            _extreme_label(axis, x, temperature, maximum, "max", tracked)
        _mark_zero_crossings(axis, x, temperature, tracked)


def _temperature_background(axis, lower: float, upper: float) -> None:
    zones = (
        (lower, -20.0, "#bfdcf5", "очень холодно"),
        (-20.0, -10.0, "#d6eafb", "сильный мороз"),
        (-10.0, 0.0, "#eaf5fd", "мороз"),
        (0.0, 25.0, "#ffffff", ""),
        (25.0, 30.0, "#fff7d6", "тепло"),
        (30.0, 35.0, "#ffe6bd", "жара"),
        (35.0, upper, "#ffd0ca", "очень жарко"),
    )
    for start, end, color, label in zones:
        visible_start = max(lower, start)
        visible_end = min(upper, end)
        if visible_end <= visible_start:
            continue
        axis.axhspan(
            visible_start,
            visible_end,
            color=color,
            alpha=0.50,
            zorder=-10,
        )
        if label:
            _outside_zone_label(axis, (visible_start + visible_end) / 2, label)
    for threshold in (-20.0, -10.0, 0.0, 30.0, 35.0):
        if lower < threshold < upper:
            axis.axhline(
                threshold,
                color="#82909a" if threshold == 0 else "#b8c1c7",
                linewidth=1.0 if threshold == 0 else 0.55,
                linestyle="-" if threshold == 0 else ":",
                alpha=0.85,
                zorder=0,
            )


def _mark_zero_crossings(axis, x, values, tracked) -> None:
    last_x: float | None = None
    shown = 0
    for index in range(1, len(values)):
        left, right = values[index - 1], values[index]
        if not (np.isfinite(left) and np.isfinite(right)) or left == right:
            continue
        if not ((left < 0 <= right) or (left > 0 >= right)):
            continue
        fraction = -left / (right - left)
        crossing_x = x[index - 1] + fraction * (x[index] - x[index - 1])
        if last_x is not None and crossing_x - last_x < 0.5:
            continue
        axis.scatter(
            [crossing_x],
            [0],
            s=20,
            facecolor="white",
            edgecolor="#315b7d",
            linewidth=1.0,
            zorder=9,
        )
        label = axis.annotate(
            "через 0 °C",
            (crossing_x, 0),
            xytext=(4, 8),
            textcoords="offset points",
            fontsize=6.4,
            color="#315b7d",
            bbox={
                "boxstyle": "round,pad=0.1",
                "fc": "white",
                "ec": "none",
                "alpha": 0.92,
            },
            zorder=20,
        )
        tracked.append((label, 25))
        last_x = crossing_x
        shown += 1
        if shown >= 8:
            break


def _draw_humidity(axis, x: np.ndarray, series: MeteogramSeries, tracked) -> None:
    _base_axis(axis, "%")
    axis.axhspan(0, 40, color="#fff4d6", alpha=0.44, zorder=-10)
    axis.axhspan(40, 70, color="#f8fbfd", alpha=0.44, zorder=-10)
    axis.axhspan(70, 90, color="#e9f4fa", alpha=0.56, zorder=-10)
    axis.axhspan(90, 100, color="#cfe7f4", alpha=0.62, zorder=-10)
    for threshold in (40, 70, 90, 95):
        axis.axhline(
            threshold,
            color="#aebdc7",
            linewidth=0.55,
            linestyle=":",
            zorder=0,
        )

    humidity = np.clip(series.values("relative_humidity_2m"), 0, 100)
    if series.source.ensemble:
        _band(
            axis,
            x,
            series.statistic("relative_humidity_2m", "q10"),
            series.statistic("relative_humidity_2m", "q90"),
            COLORS["humidity"],
            0.12,
        )
        _band(
            axis,
            x,
            series.statistic("relative_humidity_2m", "q25"),
            series.statistic("relative_humidity_2m", "q75"),
            COLORS["humidity"],
            0.20,
        )
    sx, sy = _smooth(x, humidity, 0, 100)
    axis.fill_between(sx, 0, sy, color=COLORS["humidity"], alpha=0.10)
    axis.plot(
        sx,
        sy,
        color=COLORS["humidity"],
        lw=1.35,
        label="влажность, медиана" if series.source.ensemble else "влажность",
    )
    axis.set_ylim(0, 100)
    axis.set_yticks((0, 50, 100))
    _outside_zone_label(axis, 20, "сухо")
    _outside_zone_label(axis, 80, "влажно")
    _outside_zone_label(axis, 96, "очень влажно")
    _legend_above(axis, columns=2, fontsize=7.0)
    if not series.source.ensemble:
        _mark_fog_risk(axis, x, series, tracked)


def _mark_fog_risk(axis, x, series: MeteogramSeries, tracked) -> None:
    humidity = series.values("relative_humidity_2m")
    temperature = series.values("temperature_2m")
    dewpoint = series.values("dew_point_2m")
    wind = series.values("wind_speed_10m")
    weather = series.values("weather_code")
    for index in range(len(series.times)):
        code = int(round(weather[index])) if np.isfinite(weather[index]) else None
        diagnosed = (
            np.isfinite(humidity[index])
            and np.isfinite(temperature[index])
            and np.isfinite(dewpoint[index])
            and np.isfinite(wind[index])
            and humidity[index] >= 95
            and abs(temperature[index] - dewpoint[index]) <= 1.5
            and wind[index] <= 3.0
        )
        if code not in FOG_CODES and not diagnosed:
            continue
        artist = axis.annotate(
            "туман / дымка возможны",
            (x[index], 96),
            xytext=(4, -15),
            textcoords="offset points",
            fontsize=6.6,
            fontweight="bold",
            color="#315b7d",
            bbox={
                "boxstyle": "round,pad=0.18",
                "fc": "white",
                "ec": "none",
                "alpha": 0.94,
            },
            zorder=21,
        )
        tracked.append((artist, 30))
        break
