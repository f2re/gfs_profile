from __future__ import annotations

from datetime import timedelta

import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.patches import Patch

from meteogram_core import MeteogramSeries
from meteogram_plot_common import (
    PRECIPITATION_CLASSES,
    PRECIPITATION_RATE_CAP_MM_H,
    PRECIPITATION_RATE_TICKS,
    _combined_legend_above,
    _legend_above,
)

MAX_PRECIP_FACE = "#2f91c3"
MAX_PRECIP_EDGE = "#176b9a"
MAX_PRECIP_HATCH = "////"


def add_precipitation_upper_layer(axis, x: np.ndarray, series: MeteogramSeries) -> None:
    """Add a light hatched maximum/upper precipitation layer.

    Deterministic products mark the forecast interval with the greatest
    precipitation intensity in each local day. Ensemble products show q90
    intensity at every wet interval. The ensemble layer is deliberately named
    P90: it is an upper ensemble quantile, not a physical guaranteed maximum.
    """

    base = np.asarray(series.values("precipitation_intensity"), dtype=float)
    bars = None
    label = None
    overlay_values = np.full(len(series.times), np.nan, dtype=float)
    indices: np.ndarray

    if series.source.ensemble:
        q90 = np.asarray(
            series.statistic("precipitation", "q90_intensity"), dtype=float
        )
        mask = np.isfinite(q90) & (q90 > 0)
        indices = np.flatnonzero(mask)
        if indices.size:
            overlay_values = q90
            label = "верхняя оценка P90"
    else:
        indices = np.asarray(
            _daily_precipitation_max_indices(series.times, base), dtype=int
        )
        if indices.size:
            overlay_values = base
            label = "макс. за сутки"

    if label is not None and indices.size:
        width = max(float(np.nanmedian(np.diff(x))) * 0.68, 0.011)
        displayed = np.minimum(
            overlay_values[indices], PRECIPITATION_RATE_CAP_MM_H
        )
        bars = axis.bar(
            np.asarray(x)[indices],
            displayed,
            width=width,
            facecolor=to_rgba(MAX_PRECIP_FACE, 0.15),
            edgecolor=to_rgba(MAX_PRECIP_EDGE, 0.60),
            linewidth=0.55,
            hatch=MAX_PRECIP_HATCH,
            label=label,
            zorder=4,
        )
        _ensure_precipitation_scale(axis, base, overlay_values)
        if series.source.ensemble:
            probability_axis = getattr(
                axis, "_meteogram_probability_axis", None
            )
            if probability_axis is not None:
                _combined_legend_above(
                    axis,
                    probability_axis,
                    columns=6,
                    fontsize=6.0,
                    anchor_y=1.18,
                )
        else:
            _deterministic_legend(axis, label)

    axis._meteogram_precipitation_max_bars = bars  # type: ignore[attr-defined]
    axis._meteogram_precipitation_max_label = label  # type: ignore[attr-defined]


def _daily_precipitation_max_indices(times, values: np.ndarray) -> list[int]:
    """Return one real model interval with the highest rate per local day."""

    if len(times) != len(values):
        return []
    # Open-Meteo precipitation is an accumulation ending at the timestamp, so
    # the interval stamped exactly at 00:00 belongs to the preceding local day.
    dates = [(value - timedelta(microseconds=1)).date() for value in times]
    result: list[int] = []
    for day in dict.fromkeys(dates):
        candidates = [
            index
            for index, current in enumerate(dates)
            if current == day
            and np.isfinite(values[index])
            and values[index] > 0
        ]
        if candidates:
            result.append(max(candidates, key=lambda index: float(values[index])))
    return result


def _ensure_precipitation_scale(axis, base: np.ndarray, upper: np.ndarray) -> None:
    finite = np.concatenate((np.ravel(base), np.ravel(upper)))
    finite = finite[np.isfinite(finite) & (finite > 0)]
    if not finite.size:
        return
    maximum = min(float(np.nanmax(finite)), PRECIPITATION_RATE_CAP_MM_H)
    if maximum <= 0.5:
        axis_upper = 1.0
    elif maximum <= 2.0:
        axis_upper = 2.0
    elif maximum <= 5.0:
        axis_upper = 5.0
    elif maximum <= 10.0:
        axis_upper = 10.0
    else:
        axis_upper = PRECIPITATION_RATE_CAP_MM_H
    axis.set_ylim(0, max(float(axis.get_ylim()[1]), axis_upper))
    axis.set_yticks(_ticks_for_upper(float(axis.get_ylim()[1])))


def _ticks_for_upper(upper: float) -> tuple[float, ...]:
    if upper <= 1.0:
        return (0.1, 0.5, 1.0)
    if upper <= 2.0:
        return (0.1, 0.5, 1.0, 2.0)
    if upper <= 5.0:
        return (0.5, 2.0, 5.0)
    if upper <= 10.0:
        return (0.5, 2.0, 5.0, 10.0)
    return PRECIPITATION_RATE_TICKS


def _deterministic_legend(axis, max_label: str) -> None:
    handles = [
        Patch(
            facecolor=to_rgba(item[2], 0.58),
            edgecolor="none",
            label=item[1],
        )
        for item in PRECIPITATION_CLASSES
    ]
    handles.append(
        Patch(
            facecolor=to_rgba(MAX_PRECIP_FACE, 0.15),
            edgecolor=to_rgba(MAX_PRECIP_EDGE, 0.60),
            linewidth=0.55,
            hatch=MAX_PRECIP_HATCH,
            label=max_label,
        )
    )
    _legend_above(
        axis,
        handles=handles,
        labels=[item[1] for item in PRECIPITATION_CLASSES] + [max_label],
        columns=7,
        fontsize=5.8,
        anchor_y=1.18,
    )
