from __future__ import annotations

"""Повторяющиеся символы внутри зон простого маршрутного разреза.

Основной renderer остаётся единым для simple/pro. Этот слой добавляет только
в simple-режиме визуальное заполнение масок облачности, обледенения и болтанки,
чтобы границы зон читались без обращения к легенде.
"""

import math

import numpy as np

import route_profile_plot as _plot
from aviation_style import AVIATION


def symbol_positions(mask: np.ndarray, *, max_symbols: int) -> tuple[tuple[int, int], ...]:
    """Вернуть равномерно разреженные активные ячейки маски."""

    values = np.asarray(mask, dtype=bool)
    active = np.argwhere(values)
    if active.size == 0:
        return ()

    rows, cols = values.shape
    row_stride = 1 if rows <= 5 else 2
    col_stride = 1 if cols <= 8 else 2
    selected: list[tuple[int, int]] = []
    for row in range(0, rows, row_stride):
        for col in range(0, cols, col_stride):
            if values[row, col]:
                selected.append((row, col))

    if not selected:
        stride = max(1, len(active) // max(1, max_symbols))
        selected = [tuple(map(int, active[index])) for index in range(0, len(active), stride)]

    if len(selected) > max_symbols:
        stride = max(1, math.ceil(len(selected) / max_symbols))
        selected = selected[::stride][:max_symbols]
    return tuple(selected)


def _draw_symbol_fill(
    ax,
    x: np.ndarray,
    levels: np.ndarray,
    mask: np.ndarray,
    *,
    symbol: str,
    color: str,
    fontsize: float,
    alpha: float,
    max_symbols: int,
) -> None:
    for row, col in symbol_positions(mask, max_symbols=max_symbols):
        ax.text(
            float(x[col]),
            float(levels[row]),
            symbol,
            ha="center",
            va="center",
            fontsize=fontsize,
            fontweight="bold",
            color=color,
            alpha=alpha,
            zorder=9,
        )


_original_draw_cloud_layer = _plot._draw_cloud_layer
_original_draw_icing_layer = _plot._draw_icing_layer
_original_draw_turbulence_layer = _plot._draw_turbulence_layer


def _draw_cloud_layer(ax, data, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    _original_draw_cloud_layer(ax, data, x, levels, professional=professional)
    if professional or not np.any(data.cloud_mask):
        return
    _draw_symbol_fill(
        ax,
        x,
        levels,
        data.cloud_mask,
        symbol="☁",
        color=AVIATION.cloud,
        fontsize=10.5,
        alpha=0.64,
        max_symbols=24,
    )


def _draw_icing_layer(ax, data, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    _original_draw_icing_layer(ax, data, x, levels, professional=professional)
    icing_mask = data.icing_score > 0
    if professional or not np.any(icing_mask):
        return
    _draw_symbol_fill(
        ax,
        x,
        levels,
        icing_mask,
        symbol="❄",
        color=AVIATION.icing,
        fontsize=10.0,
        alpha=0.62,
        max_symbols=18,
    )


def _draw_turbulence_layer(ax, data, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    _original_draw_turbulence_layer(ax, data, x, levels, professional=professional)
    turbulence_mask = data.turbulence_score > 0
    if professional or not np.any(turbulence_mask):
        return
    _draw_symbol_fill(
        ax,
        x,
        levels,
        turbulence_mask,
        symbol="≈",
        color=AVIATION.turbulence,
        fontsize=10.0,
        alpha=0.58,
        max_symbols=20,
    )


_plot._draw_cloud_layer = _draw_cloud_layer
_plot._draw_icing_layer = _draw_icing_layer
_plot._draw_turbulence_layer = _draw_turbulence_layer

write_route_profile_png = _plot.write_route_profile_png
