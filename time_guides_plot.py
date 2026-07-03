from __future__ import annotations

from plot_style import METEO
from time_blocks import utc_day_blocks


def draw_utc_day_guides(ax, items, y_date: float, *, fontsize: float = 8.0) -> None:
    if not items:
        return
    from matplotlib.transforms import blended_transform_factory

    segments = utc_day_blocks(items)
    n_cols = len(items)
    transform = blended_transform_factory(ax.transData, ax.transAxes)
    for idx, (label, start, end) in enumerate(segments):
        x0 = start - 0.5
        x1 = end + 0.5
        center = (start + end) / 2.0
        span = end - start + 1
        if idx % 2 == 1:
            ax.axvspan(x0, x1, color="#000000", alpha=0.025, linewidth=0, zorder=0.6)
        if start > 0:
            ax.axvline(x0, color="#5B6573", linewidth=1.75, alpha=0.58, zorder=6.2)
        ax.text(center, y_date, label, ha="center", va="center", fontsize=fontsize, color=METEO.axis_text, fontweight="bold", zorder=8)
        rotate = 90 if (n_cols > 25 or span <= 3) else 0
        size = 10.5 if rotate else max(11.0, min(15.5, 7.0 + span))
        ax.text(center, 0.52, label, transform=transform, ha="center", va="center", rotation=rotate, fontsize=size, color="#56606E", alpha=0.12, fontweight="bold", zorder=6.4)
