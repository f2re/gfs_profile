from __future__ import annotations

import math
import textwrap
from datetime import date, datetime, timezone as dt_timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.artist import Artist
from matplotlib.figure import Figure
from matplotlib.transforms import Bbox, blended_transform_factory
from scipy.interpolate import PchipInterpolator

from meteogram_core import MeteogramSeries, grid_distance_km

COLORS = {
    "text": "#18242d",
    "muted": "#61717c",
    "grid": "#cfd8de",
    "temperature": "#d33b2f",
    "dewpoint": "#16825d",
    "humidity": "#2477b3",
    "probability_0p1": "#1d70a2",
    "probability_1": "#6a4c93",
    "probability_5": "#b23a48",
    "wind": "#244f8f",
    "gust": "#d46a1f",
    "pressure": "#5d6870",
    "night": "#60758b",
    "cloud_low": "#9ebfd3",
    "cloud_mid": "#7f98b3",
    "cloud_high": "#b5a9c8",
}
FONT_FAMILY = ("DejaVu Sans", "Liberation Sans", "Arial", "sans-serif")
WIND_ARROWS = ("↓", "↙", "←", "↖", "↑", "↗", "→", "↘")

PRECIPITATION_RATE_CAP_MM_H = 12.0
PRECIPITATION_RATE_TICKS = (0.5, 2.0, 5.0, 10.0)
TRACE_RATE_LIMIT_MM_H = 0.1
THUNDER_CODES = frozenset({95, 96, 99})
DRIZZLE_CODES = frozenset({51, 53, 55, 56, 57})
FREEZING_CODES = frozenset({56, 57, 66, 67})
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})
SHOWER_CODES = frozenset({80, 81, 82, 85, 86})
FOG_CODES = frozenset({45, 48})

# code, label, color, lower inclusive, upper exclusive
PRECIPITATION_CLASSES = (
    ("trace", "следы", "#c8dce8", 0.0, 0.1),
    ("drizzle", "морось", "#a8d5e5", 0.1, 0.5),
    ("light", "слабые", "#72b8d6", 0.5, 2.0),
    ("moderate", "умеренные", "#2f91c3", 2.0, 5.0),
    ("heavy", "сильные", "#176b9a", 5.0, 10.0),
    ("shower", "ливень", "#0a3f6b", 10.0, math.inf),
)

def _draw_header(figure: Figure, series: MeteogramSeries, tracked) -> None:
    kind = "Ансамблевая метеограмма" if series.source.ensemble else "Метеограмма"
    members = ""
    if series.source.ensemble:
        observed = series.member_count or 0
        expected = series.expected_member_count or observed
        members = f" · {observed}/{expected} членов"
        per_time = series.values("ensemble_member_count")
        if np.isfinite(per_time).any():
            minimum = int(np.nanmin(per_time))
            if minimum < observed:
                members += f" · на сроках ≥{minimum}/{expected}"

    point_label = textwrap.shorten(
        " ".join(series.point_label.split()), width=92, placeholder="…"
    )
    title = figure.text(
        0.063,
        0.982,
        point_label,
        ha="left",
        va="top",
        fontsize=14.5,
        fontweight="bold",
        color=COLORS["text"],
    )
    subtitle = figure.text(
        0.063,
        0.950,
        f"{kind} · {series.source.model}{members}",
        ha="left",
        va="top",
        fontsize=10.0,
        fontweight="bold",
        color=COLORS["text"],
    )
    resolution = f" · {series.source.resolution}" if series.source.resolution else ""
    provider = figure.text(
        0.063,
        0.925,
        f"{series.source.provider}{resolution} · цикл не передан поставщиком",
        ha="left",
        va="top",
        fontsize=8.0,
        color=COLORS["muted"],
    )
    period = (
        f"{series.times[0]:%d.%m.%Y %H:%M} — "
        f"{series.times[-1]:%d.%m.%Y %H:%M} · "
        f"{_timezone_label(series.times[0], series.timezone)}"
    )
    period_text = figure.text(
        0.063,
        0.901,
        period,
        ha="left",
        va="top",
        fontsize=8.1,
        color=COLORS["text"],
    )
    for artist in (title, subtitle, provider, period_text):
        tracked.append((artist, 100))


def _base_axis(axis, ylabel: str) -> None:
    axis.set_axisbelow(True)
    axis.grid(axis="x", color=COLORS["grid"], linewidth=0.55, alpha=0.80)
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines["left"].set_color(COLORS["grid"])
    axis.spines["bottom"].set_color(COLORS["grid"])
    axis.tick_params(labelsize=7.3, length=2.5, colors=COLORS["text"])
    axis.set_ylabel(
        ylabel,
        rotation=0,
        labelpad=18,
        fontsize=8,
        color=COLORS["text"],
    )


def _legend_above(
    axis,
    *,
    handles=None,
    labels=None,
    columns: int = 3,
    fontsize: float = 6.8,
    anchor_y: float = 1.035,
):
    if handles is None or labels is None:
        handles, labels = axis.get_legend_handles_labels()
    if not handles:
        return None
    legend = axis.legend(
        handles,
        labels,
        loc="lower left",
        bbox_to_anchor=(0.0, anchor_y),
        borderaxespad=0.0,
        fontsize=fontsize,
        frameon=True,
        facecolor="white",
        edgecolor="none",
        framealpha=0.97,
        ncol=max(1, min(columns, len(handles))),
        handlelength=1.55,
        columnspacing=0.95,
        handletextpad=0.42,
        borderpad=0.22,
    )
    legend.set_zorder(40)
    return legend


def _combined_legend_above(
    left_axis,
    right_axis,
    *,
    columns: int = 4,
    fontsize: float = 6.5,
    anchor_y: float = 1.035,
):
    left_handles, left_labels = left_axis.get_legend_handles_labels()
    right_handles, right_labels = right_axis.get_legend_handles_labels()
    return _legend_above(
        left_axis,
        handles=left_handles + right_handles,
        labels=left_labels + right_labels,
        columns=columns,
        fontsize=fontsize,
        anchor_y=anchor_y,
    )


def _shade_night(axes, series: MeteogramSeries, x: np.ndarray) -> None:
    is_day = series.values("is_day")
    if not np.isfinite(is_day).any():
        is_day = np.array(
            [
                0.0
                if _solar_elevation(
                    value, series.requested_lat, series.requested_lon
                )
                < -0.833
                else 1.0
                for value in series.times
            ]
        )
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
                axis.axvspan(
                    edges[start],
                    edges[end],
                    color=COLORS["night"],
                    alpha=0.085,
                    linewidth=0,
                    zorder=0,
                )
            start = None


def _solar_elevation(value: datetime, latitude: float, longitude: float) -> float:
    utc = value.astimezone(dt_timezone.utc)
    day = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    gamma = 2 * np.pi / 365 * (day - 1 + (hour - 12) / 24)
    equation = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148 * np.sin(3 * gamma)
    )
    true_solar_minutes = (hour * 60 + equation + 4 * longitude) % 1440
    hour_angle = np.deg2rad(true_solar_minutes / 4 - 180)
    lat = np.deg2rad(latitude)
    cosine = (
        np.sin(lat) * np.sin(declination)
        + np.cos(lat) * np.cos(declination) * np.cos(hour_angle)
    )
    return float(np.rad2deg(np.arcsin(np.clip(cosine, -1, 1))))

def _finish_axes(figure: Figure, axes, series: MeteogramSeries, tracked) -> None:
    timezone = series.times[0].tzinfo
    duration_days = (
        series.times[-1].timestamp() - series.times[0].timestamp()
    ) / 86400.0
    axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=1, tz=timezone))
    weekdays = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

    def day_label(value, _position):
        current = mdates.num2date(value, tz=timezone)
        return f"{current:%d.%m}\n{weekdays[current.weekday()]}"

    axes[-1].xaxis.set_major_formatter(plt.FuncFormatter(day_label))
    minor_hours = (6, 12, 18) if duration_days <= 8 else (12,)
    axes[-1].xaxis.set_minor_locator(
        mdates.HourLocator(byhour=minor_hours, tz=timezone)
    )
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
    generated = datetime.now(dt_timezone.utc)
    footer1 = figure.text(
        0.063,
        0.066,
        f"Запрошено {series.requested_lat:.4f},{series.requested_lon:.4f}{grid}",
        ha="left",
        va="bottom",
        fontsize=6.35,
        color=COLORS["muted"],
    )
    footer2 = figure.text(
        0.063,
        0.042,
        (
            f"Получено {series.retrieved_at_utc:%d.%m.%Y %H:%M} UTC · "
            f"PNG {generated:%d.%m.%Y %H:%M} UTC · модельный прогноз, не наблюдение"
        ),
        ha="left",
        va="bottom",
        fontsize=6.35,
        color=COLORS["muted"],
    )
    explanation = (
        "T/Td/p — среднее; направление — круговое среднее; прочее — медиана; "
        "q25–q75/q10–q90; P — доля членов за исходный интервал"
        if series.source.ensemble
        else "непрерывные поля сглажены PCHIP; осадки показаны без сглаживания"
    )
    footer3 = figure.text(
        0.063,
        0.018,
        explanation,
        ha="left",
        va="bottom",
        fontsize=6.15,
        color=COLORS["muted"],
    )
    tracked.extend(((footer1, 100), (footer2, 100), (footer3, 100)))
    figure.subplots_adjust(left=0.078, right=0.895, top=0.855, bottom=0.145)


def _line(axis, x, values, color, width, label, style="-") -> None:
    sx, sy = _smooth(x, values)
    if len(sx):
        axis.plot(
            sx,
            sy,
            color=color,
            lw=width,
            label=label,
            linestyle=style,
            zorder=8,
        )


def _band(axis, x, lower, upper, color, alpha) -> None:
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    mask = np.isfinite(lower) & np.isfinite(upper)
    if mask.sum() >= 2:
        axis.fill_between(
            x,
            lower,
            upper,
            where=mask,
            interpolate=True,
            color=color,
            alpha=alpha,
            linewidth=0,
            zorder=2,
        )


def _smooth(x, values, lower=None, upper=None):
    values = np.asarray(values, dtype=float)
    mask = np.isfinite(values)
    if mask.sum() < 2:
        return x[mask], values[mask]
    dense = np.linspace(
        x[mask][0],
        x[mask][-1],
        min(1600, max(120, int(mask.sum() * 4))),
    )
    try:
        smooth = PchipInterpolator(
            x[mask], values[mask], extrapolate=False
        )(dense)
    except ValueError:
        dense, smooth = x[mask], values[mask]
    if lower is not None or upper is not None:
        smooth = np.clip(
            smooth,
            lower if lower is not None else -np.inf,
            upper if upper is not None else np.inf,
        )
    return dense, smooth


def _extreme_label(axis, x, values, index, prefix, tracked) -> None:
    offset = (8, 8) if index < len(x) * 0.82 else (-8, 8)
    artist = axis.annotate(
        f"{prefix} {values[index]:.1f}°".replace(".", ","),
        (x[index], values[index]),
        xytext=offset,
        textcoords="offset points",
        ha="left" if offset[0] > 0 else "right",
        va="bottom",
        fontsize=6.3,
        color=COLORS["text"],
        bbox={
            "boxstyle": "round,pad=0.15",
            "fc": "white",
            "ec": COLORS["grid"],
            "lw": 0.4,
            "alpha": 0.94,
        },
        zorder=22,
    )
    tracked.append((artist, 40))


def _outside_zone_label(axis, y: float, label: str) -> None:
    axis.text(
        1.010,
        y,
        label,
        transform=axis.get_yaxis_transform(),
        ha="left",
        va="center",
        fontsize=6.0,
        color=COLORS["muted"],
        alpha=0.92,
        clip_on=False,
        bbox={
            "boxstyle": "round,pad=0.1",
            "fc": "white",
            "ec": "none",
            "alpha": 0.84,
        },
        zorder=20,
    )


def _resolve_overlaps(figure: Figure, tracked) -> None:
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    kept: list[tuple[Bbox, int]] = []
    # Legends are fixed layout elements. Treat them as high-priority obstacles
    # so optional annotations cannot be drawn through their white panels.
    seen_legends: set[int] = set()
    legends = list(figure.legends)
    legends.extend(axis.get_legend() for axis in figure.axes)
    for legend in legends:
        if legend is None or not legend.get_visible() or id(legend) in seen_legends:
            continue
        seen_legends.add(id(legend))
        try:
            kept.append((legend.get_window_extent(renderer=renderer).expanded(1.02, 1.06), 1000))
        except Exception:
            pass
    for artist, priority in sorted(tracked, key=lambda item: item[1], reverse=True):
        if not artist.get_visible():
            continue
        try:
            bbox = artist.get_window_extent(renderer=renderer).expanded(1.04, 1.12)
        except Exception:
            continue
        if any(
            bbox.overlaps(other)
            for other, other_priority in kept
            if other_priority >= priority
        ):
            artist.set_visible(False)
        else:
            kept.append((bbox, priority))
    figure.canvas.draw()


def _combined_finite(arrays) -> np.ndarray:
    finite = [
        np.asarray(values, dtype=float)[np.isfinite(values)]
        for values in arrays
        if np.isfinite(values).any()
    ]
    return np.concatenate(finite) if finite else np.asarray([], dtype=float)


def _interval_hours(times: list[datetime], index: int) -> float:
    if len(times) < 2:
        return 1.0
    if index > 0:
        hours = (times[index].timestamp() - times[index - 1].timestamp()) / 3600.0
    else:
        hours = (times[1].timestamp() - times[0].timestamp()) / 3600.0
    return max(hours, 0.01)


def _timezone_label(value: datetime, timezone_name: str) -> str:
    offset = value.utcoffset()
    if offset is None:
        return f"местное время ({timezone_name})"
    total_minutes = round(offset.total_seconds() / 60)
    sign = "+" if total_minutes >= 0 else "−"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"местное время ({timezone_name}, UTC{sign}{hours:02d}:{minutes:02d})"
