from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

import numpy as np
from matplotlib.artist import Artist

from meteogram_plot_common import COLORS, PRECIPITATION_RATE_CAP_MM_H

TREND_WINDOW_HOURS = 24.0
CRITICAL_MARKER_COLOR = "#b3262e"
TEMPERATURE_CRITICAL_LOW_C = -20.0
TEMPERATURE_CRITICAL_HIGH_C = 35.0
HUMIDITY_CRITICAL_PCT = 95.0
PRECIPITATION_CRITICAL_MM_H = 5.0
WIND_CRITICAL_MS = 10.0
GUST_CRITICAL_MS = 14.0


def rolling_time_mean(
    times: Sequence[datetime],
    values,
    *,
    window_hours: float = TREND_WINDOW_HOURS,
    minimum_coverage_hours: float | None = None,
) -> np.ndarray:
    """Return a centred, time-weighted moving mean on the original timestamps.

    Values are treated as representative of cells bounded by neighbouring
    timestamp midpoints. This avoids bias when the source changes time step.
    Missing values do not count as zero; a result is emitted only when at least
    two finite samples and a minimum temporal coverage are available.
    """

    values = np.asarray(values, dtype=float)
    if len(times) != len(values):
        raise ValueError("Временная шкала и ряд должны иметь одинаковую длину")
    result = np.full(values.shape, np.nan, dtype=float)
    if not len(values):
        return result
    if window_hours <= 0:
        raise ValueError("Окно среднего должно быть положительным")

    seconds = np.asarray([item.timestamp() for item in times], dtype=float)
    if len(seconds) > 1 and np.any(np.diff(seconds) <= 0):
        raise ValueError("Временная шкала должна строго возрастать")
    if len(seconds) == 1:
        # A single timestamp cannot support a 24-hour temporal mean.
        return result

    midpoints = (seconds[:-1] + seconds[1:]) / 2.0
    edges = np.empty(len(seconds) + 1, dtype=float)
    edges[1:-1] = midpoints
    edges[0] = seconds[0] - (seconds[1] - seconds[0]) / 2.0
    edges[-1] = seconds[-1] + (seconds[-1] - seconds[-2]) / 2.0

    window_seconds = float(window_hours) * 3600.0
    half_window = window_seconds / 2.0
    if minimum_coverage_hours is None:
        minimum_coverage_hours = min(window_hours, max(1.0, window_hours * 0.75))
    minimum_coverage = max(0.0, float(minimum_coverage_hours)) * 3600.0
    finite = np.isfinite(values)

    for index, centre in enumerate(seconds):
        left = centre - half_window
        right = centre + half_window
        overlap = np.maximum(
            0.0,
            np.minimum(edges[1:], right) - np.maximum(edges[:-1], left),
        )
        valid = finite & (overlap > 0)
        coverage = float(np.sum(overlap[valid]))
        if np.count_nonzero(valid) < 2 or coverage < minimum_coverage:
            continue
        result[index] = float(np.sum(values[valid] * overlap[valid]) / coverage)
    return result


def daily_temperature_extrema(
    times: Sequence[datetime], values
) -> list[tuple[date, int, int]]:
    """Return local-date, minimum index and maximum index for finite values."""

    values = np.asarray(values, dtype=float)
    if len(times) != len(values):
        raise ValueError("Временная шкала и ряд должны иметь одинаковую длину")
    grouped: dict[date, list[int]] = {}
    for index, current in enumerate(times):
        grouped.setdefault(current.date(), []).append(index)

    result: list[tuple[date, int, int]] = []
    for current_day, indices in grouped.items():
        finite_indices = [index for index in indices if np.isfinite(values[index])]
        if not finite_indices:
            continue
        subset = values[finite_indices]
        minimum = finite_indices[int(np.argmin(subset))]
        maximum = finite_indices[int(np.argmax(subset))]
        result.append((current_day, minimum, maximum))
    return result


def critical_episode_indices(values, mask, *, mode: str = "max") -> list[int]:
    """Select one representative extremum from each contiguous threshold episode."""

    values = np.asarray(values, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    if values.shape != mask.shape:
        raise ValueError("Маска критических значений не соответствует ряду")
    if mode not in {"min", "max"}:
        raise ValueError("mode должен быть min или max")
    active = mask & np.isfinite(values)
    result: list[int] = []
    start: int | None = None
    for index in range(len(values) + 1):
        enabled = index < len(values) and bool(active[index])
        if enabled and start is None:
            start = index
        if enabled or start is None:
            continue
        stop = index
        segment = values[start:stop]
        relative = int(np.argmin(segment) if mode == "min" else np.argmax(segment))
        result.append(start + relative)
        start = None
    return result


def _format_temperature(value: float) -> str:
    return f"{float(value):+.1f}".replace("-", "−").replace(".", ",")


def _trend_line(axis, x, times, values, *, color: str, label: str):
    trend = rolling_time_mean(times, values)
    artist = None
    if np.count_nonzero(np.isfinite(trend)) >= 2:
        (artist,) = axis.plot(
            x,
            trend,
            color=color,
            linewidth=1.05,
            linestyle=(0, (2.2, 2.2)),
            alpha=0.92,
            label=label,
            zorder=10,
        )
    return trend, artist


def _critical_markers(
    axis,
    x,
    values,
    mask,
    *,
    mode: str,
    display_values=None,
):
    indices = critical_episode_indices(values, mask, mode=mode)
    if not indices:
        return None
    displayed = np.asarray(values if display_values is None else display_values, dtype=float)
    return axis.scatter(
        np.asarray(x)[indices],
        displayed[indices],
        marker="D",
        s=28,
        facecolor=CRITICAL_MARKER_COLOR,
        edgecolor="white",
        linewidth=0.65,
        zorder=16,
        label="_nolegend_",
    )


def add_temperature_diagnostics(axis, x, series, tracked) -> None:
    temperature = np.asarray(series.values("temperature_2m"), dtype=float)
    trend, trend_artist = _trend_line(
        axis,
        x,
        series.times,
        temperature,
        color="#8f2d27",
        label="T, среднее за 24 ч",
    )

    extrema = daily_temperature_extrema(series.times, temperature)
    minimum_indices = [minimum for _day, minimum, _maximum in extrema]
    maximum_indices = [maximum for _day, _minimum, maximum in extrema]
    minimum_markers = None
    maximum_markers = None
    if minimum_indices:
        minimum_markers = axis.scatter(
            np.asarray(x)[minimum_indices],
            temperature[minimum_indices],
            marker="v",
            s=23,
            facecolor="#2f6f9f",
            edgecolor="white",
            linewidth=0.55,
            zorder=14,
            label="_nolegend_",
        )
    if maximum_indices:
        maximum_markers = axis.scatter(
            np.asarray(x)[maximum_indices],
            temperature[maximum_indices],
            marker="^",
            s=23,
            facecolor="#c43a31",
            edgecolor="white",
            linewidth=0.55,
            zorder=14,
            label="_nolegend_",
        )

    labels: list[Artist] = []
    for _current_day, minimum, maximum in extrema:
        entries = (
            (minimum, "мин.", -9, "top", "#285f87"),
            (maximum, "макс.", 8, "bottom", "#a82f28"),
        )
        seen: set[int] = set()
        for index, prefix, vertical_offset, vertical_alignment, color in entries:
            if index in seen:
                continue
            seen.add(index)
            if minimum == maximum:
                prefix = "мин./макс."
                vertical_offset = 8
                vertical_alignment = "bottom"
                color = COLORS["text"]
            horizontal_offset = -3 if index >= len(x) - 2 else 3
            artist = axis.annotate(
                f"{prefix} {_format_temperature(temperature[index])} °C",
                (x[index], temperature[index]),
                xytext=(horizontal_offset, vertical_offset),
                textcoords="offset points",
                ha="right" if horizontal_offset < 0 else "left",
                va=vertical_alignment,
                fontsize=5.75,
                color=color,
                bbox={
                    "boxstyle": "round,pad=0.10",
                    "fc": "white",
                    "ec": "none",
                    "alpha": 0.86,
                },
                zorder=22,
            )
            labels.append(artist)
            tracked.append((artist, 24))
            if minimum == maximum:
                break

    low_markers = _critical_markers(
        axis,
        x,
        temperature,
        temperature <= TEMPERATURE_CRITICAL_LOW_C,
        mode="min",
    )
    high_markers = _critical_markers(
        axis,
        x,
        temperature,
        temperature >= TEMPERATURE_CRITICAL_HIGH_C,
        mode="max",
    )

    axis._meteogram_temperature_trend = trend
    axis._meteogram_temperature_trend_artist = trend_artist
    axis._meteogram_daily_temperature_labels = labels
    axis._meteogram_daily_minimum_markers = minimum_markers
    axis._meteogram_daily_maximum_markers = maximum_markers
    axis._meteogram_temperature_critical_markers = (low_markers, high_markers)


def add_humidity_diagnostics(axis, x, series) -> None:
    humidity = np.clip(
        np.asarray(series.values("relative_humidity_2m"), dtype=float), 0, 100
    )
    trend, trend_artist = _trend_line(
        axis,
        x,
        series.times,
        humidity,
        color="#195d8d",
        label="RH, среднее за 24 ч",
    )
    markers = _critical_markers(
        axis,
        x,
        humidity,
        humidity >= HUMIDITY_CRITICAL_PCT,
        mode="max",
    )
    axis._meteogram_humidity_trend = trend
    axis._meteogram_humidity_trend_artist = trend_artist
    axis._meteogram_humidity_critical_markers = markers


def add_precipitation_diagnostics(axis, x, values) -> None:
    values = np.asarray(values, dtype=float)
    displayed = np.minimum(values, PRECIPITATION_RATE_CAP_MM_H)
    markers = _critical_markers(
        axis,
        x,
        values,
        values >= PRECIPITATION_CRITICAL_MM_H,
        mode="max",
        display_values=displayed,
    )
    axis._meteogram_precipitation_critical_markers = markers


def add_wind_pressure_diagnostics(axis, pressure_axis, x, series) -> None:
    wind = np.asarray(series.values("wind_speed_10m"), dtype=float)
    gust = np.asarray(series.values("wind_gusts_10m"), dtype=float)
    wind_trend, wind_trend_artist = _trend_line(
        axis,
        x,
        series.times,
        wind,
        color="#183f76",
        label="ветер, среднее за 24 ч",
    )
    wind_markers = _critical_markers(
        axis,
        x,
        wind,
        wind >= WIND_CRITICAL_MS,
        mode="max",
    )
    gust_markers = _critical_markers(
        axis,
        x,
        gust,
        gust >= GUST_CRITICAL_MS,
        mode="max",
    )

    pressure_trend = np.full(wind.shape, np.nan, dtype=float)
    pressure_trend_artist = None
    if pressure_axis is not None:
        pressure = np.asarray(series.values("pressure_msl"), dtype=float)
        pressure_trend, pressure_trend_artist = _trend_line(
            pressure_axis,
            x,
            series.times,
            pressure,
            color="#3f4a52",
            label="давление, среднее за 24 ч",
        )
        pressure_axis._meteogram_pressure_trend = pressure_trend
        pressure_axis._meteogram_pressure_trend_artist = pressure_trend_artist

    axis._meteogram_wind_trend = wind_trend
    axis._meteogram_wind_trend_artist = wind_trend_artist
    axis._meteogram_wind_critical_markers = wind_markers
    axis._meteogram_gust_critical_markers = gust_markers
    axis._meteogram_pressure_axis = pressure_axis
