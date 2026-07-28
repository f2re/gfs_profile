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


def _draw_card_shell(ax, group, score: int, *, rounded: bool) -> None:
    from matplotlib.patches import FancyBboxPatch, Rectangle

    xmin, xmax = ax.get_xlim()
    total = max(1.0, xmax - xmin)
    x0 = (group.start_km - xmin) / total
    width = max(0.001, (group.end_km - group.start_km) / total)
    inset = min(0.002, width * 0.08)
    patch_class = FancyBboxPatch if rounded else Rectangle
    kwargs = {
        "transform": ax.transAxes,
        "facecolor": risk_color(score, soft=True),
        "edgecolor": risk_color(score) if rounded else "#FFFFFF",
        "linewidth": 0.85 if rounded else 1.0,
        "alpha": 0.96,
    }
    if rounded:
        patch = patch_class(
            (x0 + inset, 0.04),
            max(0.001, width - 2.0 * inset),
            0.91,
            boxstyle="round,pad=0.008,rounding_size=0.025",
            **kwargs,
        )
    else:
        patch = patch_class((x0 + inset, 0.04), max(0.001, width - 2.0 * inset), 0.91, **kwargs)
    ax.add_patch(patch)


def _summary_text(assessment: CardAssessment) -> str:
    if not assessment.hazards:
        return "значимых рисков нет"
    parts = [f"{hazard.label} {hazard.fraction * 100:.0f}%" for hazard in assessment.hazards[:2]]
    if assessment.peak_score > assessment.score:
        parts.append("локальный пик")
    return " · ".join(parts)


def draw_simple_cards(ax, data: RouteProfileData) -> None:
    groups = route_card_groups(data)
    ax.set_xlim(0.0, max(1.0, data.total_distance_km))
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")
    ax.set_title("Оценка маршрута по существенным участкам", loc="left", fontsize=9.4, fontweight="bold", pad=6)

    for group in groups:
        assessment = assess_group(data, group)
        point = data.waypoints[group.start_index]
        _draw_card_shell(ax, group, assessment.score, rounded=True)
        ax.text(group.center_km, 0.86, f"{group.start_km:.0f}–{group.end_km:.0f} км", ha="center", va="center", fontsize=6.4, color=PALETTE.muted)
        ax.text(group.center_km, 0.69, risk_label(assessment.score).replace("ВЫСОКИЙ РИСК", "ВЫСОКИЙ\nРИСК"), ha="center", va="center", fontsize=7.8, fontweight="bold", color=risk_color(assessment.score), linespacing=0.9)

        width = max(1.0, group.end_km - group.start_km)
        hazards = assessment.hazards[:2]
        if hazards:
            for index, hazard in enumerate(hazards):
                x_value = group.start_km + width * (index + 1) / (len(hazards) + 1)
                draw_hazard_icon(ax, hazard.key, x_value, 0.46, size=0.036, transform=ax.get_xaxis_transform(), color=hazard.color, zorder=8)
        else:
            ax.text(group.center_km, 0.46, "✓", ha="center", va="center", fontsize=12, color=AVIATION.safe, fontweight="bold")

        ax.text(group.center_km, 0.25, _summary_text(assessment), ha="center", va="center", fontsize=5.9, color=PALETTE.muted)
        ax.text(group.center_km, 0.10, f"+{point.lead_hour} ч · {point.valid_time_utc:%d.%m %H}Z", ha="center", va="center", fontsize=5.9, color=PALETTE.text)

    ax.text(0.5, -0.08, "Расстояние · срок GFS и UTC-время начала участка", transform=ax.transAxes, ha="center", va="top", fontsize=8.0, color=PALETTE.text)


def draw_professional_cards(ax, data: RouteProfileData) -> None:
    groups = route_card_groups(data)
    ax.set_xlim(0.0, max(1.0, data.total_distance_km))
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")
    ax.set_title("Профессиональная сводка участков", loc="left", fontsize=9.4, fontweight="bold", pad=6)

    for group in groups:
        assessment = assess_group(data, group)
        point = data.waypoints[group.start_index]
        _draw_card_shell(ax, group, assessment.score, rounded=False)
        ax.text(group.center_km, 0.87, f"{group.start_km:.0f}–{group.end_km:.0f} км", ha="center", va="center", fontsize=6.2, color=PALETTE.muted)
        peak_note = f" · peak R{assessment.peak_score}" if assessment.peak_score > assessment.score else ""
        ax.text(group.center_km, 0.72, f"R{assessment.score} · {risk_label(assessment.score, short=True)}{peak_note}", ha="center", va="center", fontsize=6.5, fontweight="bold", color=risk_color(assessment.score))

        indices = group.point_indices
        wind_values = np.asarray(data.wind_speed_ms[:, indices], dtype=float)
        finite_wind = wind_values[np.isfinite(wind_values)]
        vmax = float(np.max(finite_wind)) if finite_wind.size else 0.0
        precip = max((data.waypoints[index].surface.precip_mm or 0.0) for index in indices)
        vis_values = [data.waypoints[index].surface.visibility_km for index in indices if data.waypoints[index].surface.visibility_km is not None]
        ceiling_values = [data.waypoints[index].surface.ceiling_m for index in indices if data.waypoints[index].surface.ceiling_m is not None]
        vis = min(vis_values) if vis_values else None
        ceiling = min(ceiling_values) if ceiling_values else None
        ax.text(
            group.center_km,
            0.43,
            f"Vmax {vmax:.0f} м/с · P {precip:.1f} мм\nVIS {'—' if vis is None else f'{vis:.0f} км'} · ВНГО {'—' if ceiling is None else f'{ceiling:.0f} м'}",
            ha="center",
            va="center",
            fontsize=5.4,
            color=PALETTE.muted,
            linespacing=1.12,
        )
        ax.text(group.center_km, 0.10, f"+{point.lead_hour} ч · {point.valid_time_utc:%d.%m %H}Z", ha="center", va="center", fontsize=5.9, color=PALETTE.text)

    ax.text(0.5, -0.08, "Расстояние · срок GFS и UTC-время начала участка", transform=ax.transAxes, ha="center", va="top", fontsize=8.0, color=PALETTE.text)
