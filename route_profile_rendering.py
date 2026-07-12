from __future__ import annotations

"""Two-mode route-profile renderer.

Objective GFS fields and point risk are shared. ``simple`` uses restrained
display smoothing and pictograms; ``pro`` keeps an academic layer set. Card
boundaries and aggregated risk are identical in both modes.
"""

from pathlib import Path

import numpy as np

from aviation_style import risk_color
import route_profile_plot as _legacy
import route_profile_render_common as _common
import route_profile_render_professional as _professional
import route_profile_render_simple as _simple
import route_profile_card_policy as _cards
from route_profile import RouteProfileData
from route_profile_icons import draw_hazard_icon
from route_profile_render_common import confirmed_thunder
from route_profile_visual_style import PALETTE

ROUTE_RENDER_FEATURES: dict[str, frozenset[str]] = {
    "simple": frozenset(
        {
            "dense_display_grid",
            "topology_preserving_smoothing",
            "continuous_temperature_gradient",
            "soft_cloud_masses",
            "vector_pictograms",
            "significant_hazard_fills",
            "sparse_wind_markers",
            "robust_segment_cards",
        }
    ),
    "pro": frozenset(
        {
            "light_temperature_background",
            "rh_80_90_contours",
            "red_isotherms",
            "wind_isotachs",
            "wind_barbs",
            "discrete_hazard_hatching",
            "numeric_cards",
            "robust_segment_cards",
        }
    ),
}


def render_features(mode: str) -> frozenset[str]:
    name = str(mode).strip().lower()
    if name not in ROUTE_RENDER_FEATURES:
        raise ValueError("mode must be simple or pro")
    return ROUTE_RENDER_FEATURES[name]


def _draw_card_shell(ax, group, score: int, *, rounded: bool) -> None:
    from matplotlib.patches import FancyBboxPatch, Rectangle

    xmin, xmax = ax.get_xlim()
    total = max(1.0, xmax - xmin)
    x0 = (group.start_km - xmin) / total
    width = max(0.001, (group.end_km - group.start_km) / total)
    inset = min(0.002, width * 0.08)
    if rounded:
        patch = FancyBboxPatch(
            (x0 + inset, 0.04),
            max(0.001, width - 2.0 * inset),
            0.91,
            transform=ax.transAxes,
            boxstyle="round,pad=0.008,rounding_size=0.025",
            facecolor=risk_color(score, soft=True),
            edgecolor=risk_color(score),
            linewidth=0.85,
            alpha=0.96,
        )
    else:
        patch = Rectangle(
            (x0 + inset, 0.04),
            max(0.001, width - 2.0 * inset),
            0.91,
            transform=ax.transAxes,
            facecolor=risk_color(score, soft=True),
            edgecolor="#FFFFFF",
            linewidth=1.0,
        )
    ax.add_patch(patch)


def _surface_masks(data: RouteProfileData) -> tuple[np.ndarray, np.ndarray]:
    thunder = np.asarray([confirmed_thunder(point) for point in data.waypoints], dtype=bool)
    precip = np.asarray([(point.surface.precip_mm or 0.0) >= 0.2 for point in data.waypoints], dtype=bool) & ~thunder
    return thunder, precip


def _draw_simple_surface_weather(ax, data: RouteProfileData) -> None:
    thunder, precip = _surface_masks(data)
    for mask, key, pressure, size, color in (
        (thunder, "thunder", 973.0, 0.037, PALETTE.thunder),
        (precip, "precip", 980.0, 0.026, PALETTE.precip),
    ):
        runs = _legacy._mask_runs(mask)
        if len(runs) > 5:
            runs = sorted(runs, key=lambda item: item[1] - item[0], reverse=True)[:5]
        for start, end in sorted(runs):
            center = float(np.mean([data.waypoints[index].distance_km for index in range(start, end + 1)]))
            draw_hazard_icon(ax, key, center, pressure, size=size, color=color, zorder=10)


def _draw_professional_surface_tags(ax, data: RouteProfileData) -> None:
    thunder, precip = _surface_masks(data)
    for mask, text, pressure, color in (
        (thunder, "TSRA", 970.0, PALETTE.thunder),
        (precip, "RA", 982.0, PALETTE.precip),
    ):
        runs = _legacy._mask_runs(mask)
        if len(runs) > 8:
            runs = sorted(runs, key=lambda item: item[1] - item[0], reverse=True)[:8]
        for start, end in sorted(runs):
            center = float(np.mean([data.waypoints[index].distance_km for index in range(start, end + 1)]))
            ax.text(
                center,
                pressure,
                text,
                ha="center",
                va="center",
                fontsize=5.8,
                fontweight="bold",
                color=color,
                zorder=10,
                bbox={"boxstyle": "round,pad=0.16", "fc": "white", "ec": color, "lw": 0.7, "alpha": 0.91},
            )


def write_route_profile_png(data: RouteProfileData) -> Path:
    if data.mode == "pro":
        return _professional.render_professional_route_profile(data)
    return _simple.render_simple_route_profile(data)


# Presentation-only integration patches. Objective fields and point risk are not
# modified here.
_common._draw_card_shell = _draw_card_shell
_simple._draw_surface_weather = _draw_simple_surface_weather
_professional._draw_surface_tags = _draw_professional_surface_tags
_simple.draw_simple_cards = _cards.draw_simple_cards
_professional.draw_professional_cards = _cards.draw_professional_cards
_legacy.write_route_profile_png = write_route_profile_png
