from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aviation_style import AVIATION, risk_color, risk_label
import route_profile_plot as legacy
from route_profile import RouteProfileData
from route_profile_icons import draw_hazard_icon
from route_profile_visual_style import PALETTE


@dataclass(frozen=True)
class HazardCoverage:
    key: str
    label: str
    color: str
    fraction: float


@dataclass(frozen=True)
class CardAssessment:
    score: int
    peak_score: int
    high_fraction: float
    hazards: tuple[HazardCoverage, ...]


def route_card_groups(data: RouteProfileData):
    """Create non-overlapping, mode-independent card groups."""

    points = tuple(data.waypoints)
    if not points:
        return ()
    if len(points) == 1:
        return (
            legacy.RouteDisplayGroup(
                start_index=0,
                end_index=0,
                start_km=0.0,
                end_km=max(1.0, float(data.total_distance_km)),
                center_km=float(points[0].distance_km),
                point_indices=(0,),
            ),
        )

    target_cards = int(np.clip(np.ceil(float(data.total_distance_km) / 115.0), 3, 10))
    chunks = np.array_split(np.arange(len(points), dtype=int), min(target_cards, len(points)))
    groups = []
    for group_index, chunk in enumerate(chunks):
        if not chunk.size:
            continue
        first = int(chunk[0])
        last = int(chunk[-1])
        start_km = 0.0 if group_index == 0 else (
            float(points[first - 1].distance_km) + float(points[first].distance_km)
        ) / 2.0
        end_km = float(data.total_distance_km) if group_index == len(chunks) - 1 else (
            float(points[last].distance_km) + float(points[last + 1].distance_km)
        ) / 2.0
        groups.append(
            legacy.RouteDisplayGroup(
                start_index=first,
                end_index=last,
                start_km=start_km,
                end_km=end_km,
                center_km=(start_km + end_km) / 2.0,
                point_indices=tuple(int(index) for index in chunk),
            )
        )
    return tuple(groups)


def _point_fraction(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=bool)
    return float(np.mean(values)) if values.size else 0.0


def _finite_column_max(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    valid = np.isfinite(array)
    return np.max(np.where(valid, array, -np.inf), axis=0)


def _surface_flags(data: RouteProfileData, indices: tuple[int, ...]) -> dict[str, np.ndarray]:
    points = [data.waypoints[index] for index in indices]
    return {
        "thunder": np.asarray([str(point.surface.phenomena) == "TSRA" for point in points], dtype=bool),
        "visibility": np.asarray(
            [
                (point.surface.visibility_km is not None and point.surface.visibility_km < 5.0)
                or (point.surface.ceiling_m is not None and point.surface.ceiling_m < 1000.0)
                for point in points
            ],
            dtype=bool,
        ),
        "precip": np.asarray([(point.surface.precip_mm or 0.0) >= 0.2 for point in points], dtype=bool),
    }


def assess_group(data: RouteProfileData, group) -> CardAssessment:
    indices = tuple(group.point_indices)
    scores = np.asarray([int(data.waypoints[index].risk_score) for index in indices], dtype=int)
    peak = int(np.max(scores)) if scores.size else 0
    high_count = int(np.sum(scores >= 3))
    high_fraction = _point_fraction(scores >= 3)
    moderate_fraction = _point_fraction(scores >= 2)

    surface = _surface_flags(data, indices)
    critical_surface = bool(np.any(surface["thunder"])) or any(
        (data.waypoints[index].surface.visibility_km is not None and data.waypoints[index].surface.visibility_km < 1.0)
        or (data.waypoints[index].surface.ceiling_m is not None and data.waypoints[index].surface.ceiling_m < 300.0)
        for index in indices
    )

    if critical_surface or (high_count >= 2 and high_fraction >= (1.0 / 3.0 - 1e-9)):
        score = 3
    elif peak >= 3 or moderate_fraction >= (1.0 / 3.0 - 1e-9):
        score = 2
    elif peak >= 1:
        score = 1
    else:
        score = 0

    icing = np.max(data.icing_score[:, indices], axis=0) >= 2
    turbulence = np.max(data.turbulence_score[:, indices], axis=0) >= 2
    wind = _finite_column_max(data.wind_speed_ms[:, indices]) >= 20.0
    cloud = np.any(data.cloud_mask[:, indices], axis=0)

    fractions = {
        "thunder": _point_fraction(surface["thunder"]),
        "visibility": _point_fraction(surface["visibility"]),
        "icing": _point_fraction(icing),
        "turbulence": _point_fraction(turbulence),
        "wind": _point_fraction(wind),
        "precip": _point_fraction(surface["precip"] & ~surface["thunder"]),
        "cloud": _point_fraction(cloud),
    }
    hazards: list[HazardCoverage] = []
    for key in ("thunder", "visibility", "icing", "turbulence", "wind", "precip"):
        fraction = fractions[key]
        if fraction <= 0.0:
            continue
        if key not in {"thunder", "visibility"} and fraction < 0.20:
            continue
        token = legacy._HAZARD_TOKENS[key]
        hazards.append(HazardCoverage(key, token.label, token.color, fraction))

    if not hazards and fractions["cloud"] >= 0.35:
        token = legacy._HAZARD_TOKENS["cloud"]
        hazards.append(HazardCoverage("cloud", token.label, token.color, fractions["cloud"]))

    hazards.sort(
        key=lambda item: (
            0 if item.key == "thunder" else 1 if item.key == "visibility" else 2,
            -item.fraction,
        )
    )
    return CardAssessment(score, peak, high_fraction, tuple(hazards[:3]))


def _card_axes(ax, groups, *, title: str):
    """Lay out readable cards in route order, independently of segment length."""
    ax.axis("off")
    ax.set_title(title, loc="left", fontsize=10.5, fontweight="bold", pad=8)
    if not groups:
        return ()
    columns = min(5, len(groups))
    rows = (len(groups) + columns - 1) // columns
    width = 1.0 / columns
    height = 1.0 / rows
    cards = []
    for index, group in enumerate(groups):
        row, column = divmod(index, columns)
        card = ax.inset_axes((column * width + 0.004, 1.0 - (row + 1) * height + 0.012,
                              width - 0.008, height - 0.024))
        card.set_xlim(0, 1)
        card.set_ylim(0, 1)
        card.axis("off")
        cards.append((card, group))
    ax.text(0.5, -0.05, "Участки по порядку маршрута · время UTC", transform=ax.transAxes,
            ha="center", va="top", fontsize=8.5, color=PALETTE.text)
    return cards


def _draw_card_shell(ax, score: int, *, rounded: bool) -> None:
    from matplotlib.patches import FancyBboxPatch, Rectangle

    kwargs = {
        "transform": ax.transAxes,
        "facecolor": risk_color(score, soft=True),
        "edgecolor": risk_color(score) if rounded else "#FFFFFF",
        "linewidth": 0.85 if rounded else 1.0,
        "alpha": 0.96,
    }
    if rounded:
        patch = FancyBboxPatch((0.01, 0.01), 0.98, 0.98,
                               boxstyle="round,pad=0,rounding_size=0.025", **kwargs)
    else:
        patch = Rectangle((0.01, 0.01), 0.98, 0.98, **kwargs)
    ax.add_patch(patch)


def _summary_text(assessment: CardAssessment) -> str:
    if not assessment.hazards:
        return "Значимых рисков нет"
    parts = [f"{hazard.label} {hazard.fraction * 100:.0f}%" for hazard in assessment.hazards[:2]]
    return "\n".join(parts)


def _card_caption(ax, group, point) -> None:
    ax.text(0.5, 0.90, f"{group.start_km:.0f}–{group.end_km:.0f} км", ha="center",
            va="center", fontsize=8.6, color=PALETTE.muted)
    ax.text(0.5, 0.09, f"+{point.lead_hour} ч · {point.valid_time_utc:%d.%m %H}Z",
            ha="center", va="center", fontsize=8.2, color=PALETTE.text)


def draw_simple_cards(ax, data: RouteProfileData) -> None:
    cards = _card_axes(ax, route_card_groups(data), title="Участки маршрута")
    for card, group in cards:
        assessment = assess_group(data, group)
        point = data.waypoints[group.start_index]
        _draw_card_shell(card, assessment.score, rounded=True)
        _card_caption(card, group, point)
        peak_note = f" · пик R{assessment.peak_score}" if assessment.peak_score > assessment.score else ""
        card.text(0.5, 0.74, risk_label(assessment.score) + peak_note, ha="center", va="center",
                  fontsize=10, fontweight="bold", color=risk_color(assessment.score))
        hazards = assessment.hazards[:2]
        if hazards:
            for index, hazard in enumerate(hazards):
                draw_hazard_icon(card, hazard.key, (index + 1) / (len(hazards) + 1), 0.52,
                                 size=16, transform=card.transAxes, color=hazard.color, zorder=8)
        else:
            card.text(0.5, 0.52, "✓", ha="center", va="center", fontsize=16,
                      color=AVIATION.safe, fontweight="bold")
        card.text(0.5, 0.28, _summary_text(assessment), ha="center", va="center",
                  fontsize=8.4, color=PALETTE.muted, linespacing=1.12)


def draw_professional_cards(ax, data: RouteProfileData) -> None:
    cards = _card_axes(ax, route_card_groups(data), title="Сводка по участкам")
    for card, group in cards:
        assessment = assess_group(data, group)
        point = data.waypoints[group.start_index]
        _draw_card_shell(card, assessment.score, rounded=False)
        _card_caption(card, group, point)
        peak_note = f"\nЛокальный пик R{assessment.peak_score}" if assessment.peak_score > assessment.score else ""
        card.text(0.5, 0.72, f"R{assessment.score} · {risk_label(assessment.score, short=True)}{peak_note}",
                  ha="center", va="center", fontsize=9.5, fontweight="bold",
                  color=risk_color(assessment.score), linespacing=1.12)

        indices = group.point_indices
        wind_values = np.asarray(data.wind_speed_ms[:, indices], dtype=float)
        finite_wind = wind_values[np.isfinite(wind_values)]
        vmax = float(np.max(finite_wind)) if finite_wind.size else 0.0
        precip = max((data.waypoints[index].surface.precip_mm or 0.0) for index in indices)
        vis_values = [data.waypoints[index].surface.visibility_km for index in indices if data.waypoints[index].surface.visibility_km is not None]
        ceiling_values = [data.waypoints[index].surface.ceiling_m for index in indices if data.waypoints[index].surface.ceiling_m is not None]
        vis = min(vis_values) if vis_values else None
        ceiling = min(ceiling_values) if ceiling_values else None
        card.text(0.5, 0.40,
                  f"Vmax {vmax:.0f} м/с · P {precip:.1f} мм\n"
                  f"VIS {'—' if vis is None else f'{vis:.0f} км'} · ВНГО {'—' if ceiling is None else f'{ceiling:.0f} м'}",
                  ha="center", va="center", fontsize=8.4, color=PALETTE.muted, linespacing=1.3)
