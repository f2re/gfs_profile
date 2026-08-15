from __future__ import annotations

import os
import tempfile
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.artist import Artist
from matplotlib.figure import Figure

from meteogram_core import MeteogramError, MeteogramSeries
from meteogram_plot_common import (
    COLORS,
    FONT_FAMILY,
    PRECIPITATION_RATE_CAP_MM_H,
    PRECIPITATION_RATE_TICKS,
    TRACE_RATE_LIMIT_MM_H,
    _draw_header,
    _finish_axes,
    _resolve_overlaps,
    _shade_night,
)
from meteogram_plot_thermo import _draw_clouds, _draw_humidity, _draw_temperature
from meteogram_plot_weather import _draw_precipitation, _draw_wind_pressure
from meteogram_precip_style import add_precipitation_upper_layer


def write_meteogram_png(
    series: MeteogramSeries,
    output_path: Path | None = None,
) -> Path:
    figure, _axes, _tracked = build_meteogram_figure(series)
    dpi = int(os.getenv("METEOGRAM_DPI", "170"))
    if output_path is None:
        handle = tempfile.NamedTemporaryFile(
            prefix="meteogram_", suffix=".png", delete=False
        )
        handle.close()
        output_path = Path(handle.name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=dpi, facecolor="white")
    plt.close(figure)
    return output_path


def build_meteogram_figure(
    series: MeteogramSeries,
) -> tuple[Figure, tuple, list[tuple[Artist, int]]]:
    if len(series.times) < 2:
        raise MeteogramError("Для метеограммы требуется не менее двух сроков")

    duration_days = (
        series.times[-1].timestamp() - series.times[0].timestamp()
    ) / 86400.0
    width = min(17.0, max(11.8, 9.0 + duration_days * 0.48))
    dpi = int(os.getenv("METEOGRAM_DPI", "170"))
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": list(FONT_FAMILY[:-1]),
            "axes.edgecolor": COLORS["grid"],
            "axes.labelcolor": COLORS["text"],
            "xtick.color": COLORS["text"],
            "ytick.color": COLORS["text"],
            "text.color": COLORS["text"],
        }
    )
    figure, raw_axes = plt.subplots(
        5,
        1,
        figsize=(width, 7.75),
        dpi=dpi,
        sharex=True,
        gridspec_kw={
            "height_ratios": (0.90, 1.55, 0.82, 1.38, 1.48),
            "hspace": 0.46,
        },
    )
    axes = tuple(raw_axes)
    figure.patch.set_facecolor("white")
    x = mdates.date2num(series.times)
    tracked: list[tuple[Artist, int]] = []

    _draw_header(figure, series, tracked)
    _shade_night(axes, series, x)
    _draw_clouds(axes[0], x, series)
    _draw_temperature(axes[1], x, series, tracked)
    _draw_humidity(axes[2], x, series, tracked)
    _draw_precipitation(axes[3], x, series, tracked)
    add_precipitation_upper_layer(axes[3], x, series)
    _draw_wind_pressure(axes[4], x, series, tracked)
    _finish_axes(figure, axes, series, tracked)
    _resolve_overlaps(figure, tracked)
    return figure, axes, tracked


def audit_meteogram_layout(path: Path) -> dict[str, int]:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
    aspect_ratio = max(width, height) / max(1, min(width, height))
    photo_safe = int(width + height <= 10_000 and aspect_ratio <= 20.0)
    return {
        "width": width,
        "height": height,
        "dimension_sum": width + height,
        "photo_safe": photo_safe,
    }
