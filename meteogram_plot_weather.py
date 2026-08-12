from __future__ import annotations

import math
from datetime import datetime, time, timedelta

import numpy as np
from matplotlib.artist import Artist
from matplotlib.colors import to_rgba
from matplotlib.patches import Patch
from matplotlib.transforms import blended_transform_factory

from meteogram_core import MeteogramSeries
from meteogram_diagnostics import add_precipitation_diagnostics, add_wind_pressure_diagnostics
from meteogram_plot_common import (
    COLORS,
    DRIZZLE_CODES,
    FREEZING_CODES,
    PRECIPITATION_CLASSES,
    PRECIPITATION_RATE_CAP_MM_H,
    PRECIPITATION_RATE_TICKS,
    SHOWER_CODES,
    SNOW_CODES,
    THUNDER_CODES,
    TRACE_RATE_LIMIT_MM_H,
    WIND_ARROWS,
    _band,
    _base_axis,
    _combined_finite,
    _combined_legend_above,
    _interval_hours,
    _legend_above,
    _line,
)

def _draw_precipitation(axis, x: np.ndarray, series: MeteogramSeries, tracked) -> None:
    _base_axis(axis, "мм/ч")
    values = series.values("precipitation_intensity")
    if series.source.ensemble:
        q50 = series.statistic("precipitation", "q50_intensity")
        values = q50 if np.isfinite(q50).any() else values
    values = np.asarray(values, dtype=float)
    display_values = np.minimum(
        np.nan_to_num(values, nan=0.0), PRECIPITATION_RATE_CAP_MM_H
    )
    trace_mask = (
        np.isfinite(values) & (values > 0) & (values < TRACE_RATE_LIMIT_MM_H)
    )
    bar_heights = display_values.copy()
    bar_heights[trace_mask] = 0.0

    weather = series.values("weather_code")
    classes = [
        _precipitation_class(
            values[index] if np.isfinite(values[index]) else 0.0,
            int(round(weather[index])) if np.isfinite(weather[index]) else None,
        )
        for index in range(len(values))
    ]
    facecolors = [to_rgba(item[2], 0.86) for item in classes]
    thunder_flags = [
        not series.source.ensemble
        and np.isfinite(weather[index])
        and int(round(weather[index])) in THUNDER_CODES
        and bar_heights[index] > 0
        for index in range(len(values))
    ]
    edgecolors = [
        to_rgba("#6a1b9a", 1.0) if thunder else (0.0, 0.0, 0.0, 0.0)
        for thunder in thunder_flags
    ]
    linewidths = [0.9 if thunder else 0.0 for thunder in thunder_flags]

    _precipitation_background(axis)
    width = max(np.nanmedian(np.diff(x)) * 0.82, 0.015)
    bars = axis.bar(
        x,
        bar_heights,
        width=width,
        color=facecolors,
        edgecolor=edgecolors,
        linewidth=linewidths,
        label=(
            "медиана интенсивности"
            if series.source.ensemble
            else "интенсивность осадков"
        ),
        zorder=3,
    )
    trace_markers = None
    if trace_mask.any():
        trace_markers = axis.scatter(
            x[trace_mask],
            np.full(int(trace_mask.sum()), 0.12),
            marker="_",
            s=34,
            linewidths=1.6,
            color=[classes[index][2] for index in np.flatnonzero(trace_mask)],
            zorder=6,
            label="следы <0,1 мм/ч",
        )

    axis.set_ylim(0, PRECIPITATION_RATE_CAP_MM_H)
    axis.set_yticks(PRECIPITATION_RATE_TICKS)
    for index, rate in enumerate(values):
        if np.isfinite(rate) and rate > PRECIPITATION_RATE_CAP_MM_H:
            artist = axis.annotate(
                f"↑ {rate:.1f}".replace(".", ","),
                (x[index], PRECIPITATION_RATE_CAP_MM_H),
                xytext=(0, -11),
                textcoords="offset points",
                ha="center",
                fontsize=6.6,
                fontweight="bold",
                color="#0a3f6b",
                bbox={
                    "boxstyle": "round,pad=0.1",
                    "fc": "white",
                    "ec": "none",
                    "alpha": 0.94,
                },
                zorder=21,
            )
            tracked.append((artist, 35))

    daily_labels = _daily_precipitation_labels(axis, x, series, values, tracked)
    add_precipitation_diagnostics(axis, x, values)
    probability_axis = None
    if series.source.ensemble:
        probability_axis = axis.twinx()
        probability_axis.spines["top"].set_visible(False)
        for code, label, color in (
            ("precipitation_probability_0p1", "P ≥0,1 мм/интервал", COLORS["probability_0p1"]),
            ("precipitation_probability_1", "P ≥1 мм/интервал", COLORS["probability_1"]),
            ("precipitation_probability_5", "P ≥5 мм/интервал", COLORS["probability_5"]),
        ):
            probability = series.values(code)
            if not np.isfinite(probability).any():
                continue
            probability_axis.step(
                x,
                probability,
                where="mid",
                color=color,
                linewidth=1.15,
                label=label,
                zorder=5,
            )
        probability_axis.set_ylim(0, 100)
        probability_axis.set_ylabel("%", fontsize=8, rotation=0, labelpad=12)
        probability_axis.tick_params(axis="y", labelsize=7)
        _combined_legend_above(
            axis,
            probability_axis,
            columns=5,
            fontsize=6.35,
            anchor_y=1.18,
        )
    else:
        handles = [
            Patch(
                facecolor=to_rgba(item[2], 0.86),
                edgecolor="none",
                label=item[1],
            )
            for item in PRECIPITATION_CLASSES
        ]
        _legend_above(
            axis,
            handles=handles,
            labels=[item[1] for item in PRECIPITATION_CLASSES],
            columns=6,
            fontsize=6.20,
            anchor_y=1.18,
        )

    # Exposed for regression tests and runtime diagnostics.
    axis._meteogram_precipitation_bars = bars  # type: ignore[attr-defined]
    axis._meteogram_trace_markers = trace_markers  # type: ignore[attr-defined]
    axis._meteogram_daily_labels = daily_labels  # type: ignore[attr-defined]
    axis._meteogram_probability_axis = probability_axis  # type: ignore[attr-defined]


def _precipitation_background(axis) -> None:
    for _code, _label, color, lower, upper in PRECIPITATION_CLASSES:
        visible_upper = min(upper, PRECIPITATION_RATE_CAP_MM_H)
        if visible_upper <= lower:
            continue
        axis.axhspan(
            lower,
            visible_upper,
            color=color,
            alpha=0.075,
            zorder=-10,
        )
        if lower > 0:
            axis.axhline(
                lower,
                color=color,
                linewidth=0.55,
                linestyle=":",
                alpha=0.75,
            )


def _precipitation_class(rate: float, weather_code: int | None = None):
    # The fill colour encodes only numerical intensity. Phase and thunder are
    # separate diagnostics; a WMO code must not promote a weak rate to “ливень”.
    # Keep weather_code in the signature for compatibility with existing callers.
    _ = weather_code
    rate = max(0.0, float(rate))
    for item in PRECIPITATION_CLASSES:
        if rate < item[4]:
            return item
    return PRECIPITATION_CLASSES[-1]


def _daily_precipitation_labels(axis, x, series, rates, tracked) -> list[Artist]:
    raw = (
        series.statistic("precipitation", "q50")
        if series.source.ensemble
        else series.values("precipitation")
    )
    member_matrix = (
        np.asarray(series.statistic("precipitation", "members"), dtype=float)
        if series.source.ensemble
        else np.empty((0, len(series.times)), dtype=float)
    )
    weather = series.values("weather_code")
    # Open-Meteo precipitation is an accumulation ending at the timestamp.
    # Therefore an interval stamped exactly at 00:00 belongs to the preceding
    # local day, not to the new day.
    dates = [(value - timedelta(microseconds=1)).date() for value in series.times]
    transform = blended_transform_factory(axis.transData, axis.transAxes)
    labels: list[Artist] = []
    for day_number, day in enumerate(dict.fromkeys(dates)):
        indices = [index for index, current in enumerate(dates) if current == day]
        total = _daily_central_total(raw, member_matrix, indices)
        if not np.isfinite(total) or total < 0.1:
            continue
        coverage_hours = sum(
            _interval_hours(series.times, index)
            for index in indices
            if np.isfinite(raw[index])
        )
        maximum_rate = max(
            (float(rates[index]) for index in indices if np.isfinite(rates[index])),
            default=0.0,
        )
        drizzle_hours = sum(
            _interval_hours(series.times, index)
            for index in indices
            if (
                (
                    not series.source.ensemble
                    and np.isfinite(weather[index])
                    and int(round(weather[index])) in DRIZZLE_CODES
                )
                or (np.isfinite(rates[index]) and 0.05 <= rates[index] < 0.5)
            )
        )
        persistent_drizzle = drizzle_hours >= 6 and maximum_rate < 2.0
        wet_codes = [
            int(round(weather[index]))
            for index in indices
            if np.isfinite(weather[index])
            and np.isfinite(rates[index])
            and rates[index] >= 0.05
        ]
        required_codes = max(1, math.ceil(len(wet_codes) / 2))
        if (
            not series.source.ensemble
            and sum(code in THUNDER_CODES for code in wet_codes) >= required_codes
        ):
            description = "гроза"
        elif (
            not series.source.ensemble
            and sum(code in FREEZING_CODES for code in wet_codes) >= required_codes
        ):
            description = "переохлаждённые осадки"
        elif (
            not series.source.ensemble
            and sum(code in SNOW_CODES for code in wet_codes) >= required_codes
        ):
            description = "снег"
        elif maximum_rate >= 10.0:
            description = "ливень"
        elif persistent_drizzle:
            description = (
                "длительные слабые осадки"
                if series.source.ensemble
                else "длительная морось"
            )
        elif total < 1.0:
            description = "слабые осадки"
        elif total < 5.0:
            description = "небольшие осадки"
        elif total < 15.0:
            description = "заметные осадки"
        elif total < 30.0:
            description = "много осадков"
        else:
            description = "очень много осадков"
        local_day_hours = _local_day_hours(day, series.times[0].tzinfo)
        if coverage_hours >= local_day_hours - 0.05:
            amount_label = f"{total:.1f} мм/сут"
        else:
            rounded_hours = round(coverage_hours, 1)
            hours_label = f"{rounded_hours:g}".replace(".", ",")
            amount_label = f"{total:.1f} мм за {hours_label} ч"
        centre = float(np.mean(x[indices]))
        artist = axis.text(
            centre,
            1.040 if day_number % 2 == 0 else 1.075,
            f"{amount_label} · {description}".replace(".", ","),
            transform=transform,
            ha="center",
            va="bottom",
            fontsize=5.9,
            color=COLORS["text"],
            bbox={
                "boxstyle": "round,pad=0.10",
                "fc": "white",
                "ec": "none",
                "alpha": 0.90,
            },
            clip_on=False,
            zorder=30,
        )
        labels.append(artist)
        tracked.append((artist, 20))
    return labels


def _daily_central_total(
    raw: np.ndarray, member_matrix: np.ndarray, indices: list[int]
) -> float:
    if member_matrix.ndim == 2 and member_matrix.shape[1] == len(raw):
        subset = member_matrix[:, indices]
        complete = np.all(np.isfinite(subset), axis=1)
        if complete.any():
            # Median of member accumulations, not a sum of pointwise medians.
            return float(np.median(np.sum(subset[complete], axis=1)))
    values = np.asarray(raw, dtype=float)[indices]
    return float(np.nansum(values)) if np.isfinite(values).any() else float("nan")


def _local_day_hours(day, tzinfo) -> float:
    start = datetime.combine(day, time.min, tzinfo=tzinfo)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tzinfo)
    return max((end.timestamp() - start.timestamp()) / 3600.0, 0.01)


def _draw_wind_pressure(axis, x: np.ndarray, series: MeteogramSeries, tracked) -> None:
    _base_axis(axis, "м/с")
    wind = series.values("wind_speed_10m")
    gust = series.values("wind_gusts_10m")
    if series.source.ensemble:
        _band(
            axis,
            x,
            series.statistic("wind_speed_10m", "q10"),
            series.statistic("wind_speed_10m", "q90"),
            COLORS["wind"],
            0.12,
        )
    wind_label = "ветер, медиана" if series.source.ensemble else "ветер"
    gust_label = "порывы, медиана" if series.source.ensemble else "порывы"
    _line(axis, x, wind, COLORS["wind"], 1.55, wind_label)
    if np.isfinite(gust).any():
        _line(axis, x, gust, COLORS["gust"], 1.25, gust_label, "--")
    finite_wind = _combined_finite([wind, gust])
    upper = (
        max(25.0, math.ceil(float(finite_wind.max()) * 1.12 / 5.0) * 5.0)
        if finite_wind.size
        else 25.0
    )
    axis.set_ylim(0, upper)
    for threshold in (10.0, 14.0, 20.0):
        if threshold < upper:
            axis.axhline(
                threshold,
                color="#b6a089" if threshold == 14 else "#b7c0c6",
                linewidth=0.65,
                linestyle=":" if threshold != 14 else "--",
                alpha=0.85,
                zorder=0,
            )

    pressure_axis = None
    pressure = series.values("pressure_msl")
    if np.isfinite(pressure).any():
        pressure_axis = axis.twinx()
        pressure_axis.plot(
            x,
            pressure,
            color=COLORS["pressure"],
            lw=0.95,
            alpha=0.88,
            label="давление, среднее" if series.source.ensemble else "давление",
        )
        low, high = float(np.nanmin(pressure)), float(np.nanmax(pressure))
        margin = max(5.0, (high - low) * 0.30)
        pressure_axis.set_ylim(min(970.0, low - margin), max(1040.0, high + margin))
        pressure_axis.axhline(
            1013.0,
            color=COLORS["pressure"],
            linewidth=0.55,
            linestyle=":",
            alpha=0.55,
        )
        pressure_axis.set_ylabel(
            "гПа",
            rotation=0,
            labelpad=17,
            fontsize=7,
            color=COLORS["pressure"],
        )
        pressure_axis.tick_params(labelsize=6.5, colors=COLORS["pressure"])
        pressure_axis.spines["top"].set_visible(False)
    add_wind_pressure_diagnostics(axis, pressure_axis, x, series)
    _wind_arrows(axis, x, series.values("wind_direction_10m"))
    if pressure_axis is not None:
        _combined_legend_above(
            axis,
            pressure_axis,
            columns=5,
            fontsize=6.2,
            anchor_y=1.035,
        )
    else:
        _legend_above(axis, columns=3, fontsize=6.4)

    maximum_gust = float(np.nanmax(gust)) if np.isfinite(gust).any() else 0.0
    maximum_wind = float(np.nanmax(wind)) if np.isfinite(wind).any() else 0.0
    label = None
    value = 0.0
    index = 0
    if maximum_gust >= 20.0:
        label, value, index = "опасные порывы", maximum_gust, int(np.nanargmax(gust))
    elif maximum_gust >= 14.0:
        label, value, index = "сильные порывы", maximum_gust, int(np.nanargmax(gust))
    elif maximum_wind >= 10.0:
        label, value, index = "сильный ветер", maximum_wind, int(np.nanargmax(wind))
    if label is not None:
        artist = axis.annotate(
            label,
            (x[index], value),
            xytext=(5, -15 if value > upper * 0.72 else 10),
            textcoords="offset points",
            fontsize=6.5,
            fontweight="bold",
            color="#9a4d12",
            bbox={
                "boxstyle": "round,pad=0.16",
                "fc": "white",
                "ec": "none",
                "alpha": 0.94,
            },
            zorder=21,
        )
        tracked.append((artist, 30))


def _wind_arrows(axis, x: np.ndarray, direction: np.ndarray) -> None:
    step = max(1, int(np.ceil(len(x) / 32)))
    transform = blended_transform_factory(axis.transData, axis.transAxes)
    for index in range(0, len(x), step):
        if not np.isfinite(direction[index]):
            continue
        arrow = WIND_ARROWS[int(((direction[index] + 22.5) % 360) // 45)]
        axis.text(
            x[index],
            0.035,
            arrow,
            transform=transform,
            ha="center",
            va="bottom",
            fontsize=8.2,
            color=COLORS["text"],
            clip_on=True,
            fontfamily="DejaVu Sans",
            zorder=12,
        )
