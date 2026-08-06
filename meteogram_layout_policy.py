from __future__ import annotations

"""Final presentation policy for meteogram annotations.

The base renderer already performs a global bounding-box overlap pass. This
policy additionally keeps at most one numeric precipitation-peak label per
local calendar day before that pass. Daily totals (Σ) remain independent and
are still removed automatically when they collide with a higher-priority label.
"""

from collections.abc import Callable

import numpy as np

import meteogram_plot

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original: Callable = meteogram_plot._draw_precipitation

    def draw_precipitation(axis, x, series, tracked) -> None:
        start = len(tracked)
        original(axis, x, series, tracked)

        by_day: dict[object, list[tuple[object, float]]] = {}
        for artist, priority in tracked[start:]:
            if priority != 30 or not hasattr(artist, "xy"):
                continue
            try:
                x_value, y_value = artist.xy
                nearest = int(np.nanargmin(np.abs(np.asarray(x, dtype=float) - float(x_value))))
                day = series.times[nearest].date()
                by_day.setdefault(day, []).append((artist, float(y_value)))
            except (AttributeError, TypeError, ValueError):
                continue

        for candidates in by_day.values():
            keep = max(candidates, key=lambda item: item[1])[0]
            for artist, _value in candidates:
                if artist is not keep:
                    artist.set_visible(False)

    meteogram_plot._draw_precipitation = draw_precipitation
    _INSTALLED = True
