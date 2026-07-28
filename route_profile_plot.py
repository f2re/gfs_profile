from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from aviation_style import AVIATION, risk_color, risk_label
from plot_style import METEO, add_footer, apply_meteo_rcparams, style_axis
from route_profile import RouteProfileData

_SIMPLE_Y_LEVELS = (1000, 850, 700, 500)
_PRO_Y_LEVELS = (1000, 925, 850, 700, 600, 500)
_MAX_SIMPLE_CARDS = 8
_MAX_PRO_CARDS = 12
_TEMP_BOUNDS_C = (-70, -50, -35, -20, -10, 0, 10, 25, 40)
_TEMP_COLORS = (
    "#D6E1EF",
    "#E0EAF3",
    "#E8F0F2",
    "#EEF3EC",
    "#F3F3E7",
    "#F7F1DE",
    "#F6E9DC",
    "#F2DDD5",
)
_ISOTHERM_COLORS = {0.0: "#D32F2F", -10.0: "#C62828", -20.0: "#A51D1D"}


@dataclass(frozen=True)
class RouteDisplayGroup:
    start_index: int
    end_index: int
    start_km: float
    end_km: float
    center_km: float
    point_indices: tuple[int, ...]


@dataclass(frozen=True)
class HazardToken:
    key: str
    symbol: str
    label: str
    color: str


_HAZARD_TOKENS = {
    "thunder": HazardToken("thunder", "⚡", "гроза", AVIATION.convection),
    "icing": HazardToken("icing", "❄", "лёд", AVIATION.icing),
    "turbulence": HazardToken("turbulence", "≈", "болтанка", AVIATION.turbulence),
    "wind": HazardToken("wind", "➤", "сильный ветер", AVIATION.wind),
    "visibility": HazardToken("visibility", "◉", "низкая видимость", AVIATION.high_risk),
    "precip": HazardToken("precip", "●", "осадки", "#3F73B8"),
    "cloud": HazardToken("cloud", "☁", "облачно", AVIATION.cloud),
}


def _safe_suffix(data: RouteProfileData) -> str:
    value = f"_{data.mode}_{data.run.date}_{data.run.cycle}_f{data.departure_lead:03d}_{data.speed_kmh}kmh.png"
    return value.replace("-", "m").replace(" ", "_")


def _x_values(data: RouteProfileData) -> np.ndarray:
    return np.asarray([point.distance_km for point in data.waypoints], dtype=float)


def _short_label(value: str, limit: int = 34) -> str:
    label = str(value or "").split(",", 1)[0].strip() or str(value or "").strip() or "точка"
    return label if len(label) <= limit else label[: limit - 1].rstrip() + "…"


def _finite_max(values: np.ndarray, default: float = 0.0) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.max(finite)) if finite.size else float(default)


def _finite_min(values: np.ndarray, default: float = 0.0) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.min(finite)) if finite.size else float(default)


def format_wind_speed_ms(value_ms: float) -> str:
    return f"{float(value_ms):.0f} м/с"


def _temperature_cmap_and_norm():
    from matplotlib.colors import BoundaryNorm, ListedColormap

    cmap = ListedColormap(_TEMP_COLORS, name="route_temperature_muted")
    cmap.set_bad("#F6F8FA")
    return cmap, BoundaryNorm(_TEMP_BOUNDS_C, cmap.N, clip=True)


def _display_groups(data: RouteProfileData, max_cards: int = _MAX_PRO_CARDS) -> tuple[RouteDisplayGroup, ...]:
    """Aggregate adjacent route legs into no more than max_cards readable cards."""

    n_points = len(data.waypoints)
    if n_points == 0:
        return ()
    if n_points == 1:
        point = data.waypoints[0]
        return (RouteDisplayGroup(0, 0, 0.0, max(1.0, data.total_distance_km), point.distance_km, (0,)),)

    n_legs = n_points - 1
    n_cards = max(1, min(int(max_cards), n_legs))
    chunks = np.array_split(np.arange(n_legs, dtype=int), n_cards)
    groups: list[RouteDisplayGroup] = []
    for chunk in chunks:
        if chunk.size == 0:
            continue
        start_leg = int(chunk[0])
        end_leg = int(chunk[-1])
        start_km = float(data.waypoints[start_leg].distance_km)
        end_km = float(data.waypoints[end_leg + 1].distance_km)
        groups.append(
            RouteDisplayGroup(
                start_index=start_leg,
                end_index=end_leg + 1,
                start_km=start_km,
                end_km=end_km,
                center_km=(start_km + end_km) / 2.0,
                point_indices=tuple(range(start_leg, end_leg + 2)),
            )
        )
    return tuple(groups)


def _hazard_tokens_for_indices(data: RouteProfileData, indices: Iterable[int], limit: int = 3) -> tuple[HazardToken, ...]:
    selected = tuple(sorted(set(int(index) for index in indices)))
    if not selected:
        return ()

    points = [data.waypoints[index] for index in selected]
    thunder = any(point.surface.cb_score >= 2 or point.surface.phenomena == "TSRA" for point in points)
    icing = _finite_max(data.icing_score[:, selected]) >= 1
    turbulence = _finite_max(data.turbulence_score[:, selected]) >= 1
    strong_wind = _finite_max(data.wind_speed_ms[:, selected]) >= 20.0
    low_visibility = any(
        (point.surface.visibility_km is not None and point.surface.visibility_km < 5.0)
        or (point.surface.ceiling_m is not None and point.surface.ceiling_m < 1000.0)
        for point in points
    )
    precip = any((point.surface.precip_mm or 0.0) >= 0.2 for point in points)
    cloud = bool(np.any(data.cloud_mask[:, selected])) or any((point.surface.total_cloud_pct or 0.0) >= 60.0 for point in points)

    tokens: list[HazardToken] = []
    for key, active in (
        ("thunder", thunder),
        ("icing", icing),
        ("turbulence", turbulence),
        ("wind", strong_wind),
        ("visibility", low_visibility),
        ("precip", precip and not thunder),
        ("cloud", cloud),
    ):
        if active:
            tokens.append(_HAZARD_TOKENS[key])
        if len(tokens) >= max(1, int(limit)):
            break
    return tuple(tokens)


def _group_risk(data: RouteProfileData, group: RouteDisplayGroup) -> int:
    return int(max(data.waypoints[index].risk_score for index in group.point_indices))


def _mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    values = np.asarray(mask, dtype=bool).reshape(-1)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(values):
        if active and start is None:
            start = index
        elif not active and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(values) - 1))
    return runs


def _zone_pressure_center(mask: np.ndarray, start: int, end: int, levels: np.ndarray, fallback: float) -> float:
    local = np.asarray(mask[:, start : end + 1], dtype=bool)
    rows = np.where(np.any(local, axis=1))[0]
    if rows.size == 0:
        return fallback
    return float(np.mean(levels[rows]))


def _set_hatch_color(contour_set, color: str, linewidth: float = 0.45) -> None:
    collections = getattr(contour_set, "collections", ())
    for collection in collections:
        collection.set_edgecolor(color)
        collection.set_linewidth(linewidth)


def _annotate_zone_runs(
    ax,
    x: np.ndarray,
    levels: np.ndarray,
    mask: np.ndarray,
    *,
    symbol: str,
    label: str,
    color: str,
    fill: str = "white",
    fallback_y: float = 750.0,
    max_labels: int = 4,
) -> None:
    point_mask = np.any(np.asarray(mask, dtype=bool), axis=0)
    runs = _mask_runs(point_mask)
    if len(runs) > max_labels:
        runs = sorted(runs, key=lambda item: item[1] - item[0], reverse=True)[:max_labels]
    for start, end in runs:
        center_x = float(np.mean(x[start : end + 1]))
        center_y = _zone_pressure_center(mask, start, end, levels, fallback_y)
        ax.text(
            center_x,
            center_y,
            f"{symbol} {label}" if label else symbol,
            ha="center",
            va="center",
            fontsize=7.2 if label else 11.0,
            fontweight="bold",
            color=color,
            zorder=10,
            bbox={"boxstyle": "round,pad=0.20", "fc": fill, "ec": color, "lw": 0.8, "alpha": 0.92},
        )


def _draw_temperature_background(ax, data: RouteProfileData, x: np.ndarray, levels: np.ndarray, *, professional: bool):
    cmap, norm = _temperature_cmap_and_norm()
    return ax.pcolormesh(
        x,
        levels,
        data.temperature_c,
        cmap=cmap,
        norm=norm,
        shading="nearest",
        alpha=0.20 if professional else 0.12,
        zorder=0,
    )


def _draw_cloud_layer(ax, data: RouteProfileData, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    if not np.any(data.cloud_mask):
        return
    xx, yy = np.meshgrid(x, levels)
    cloud = np.ma.masked_where(~data.cloud_mask, np.ones_like(data.temperature_c))
    cloud_set = ax.contourf(
        xx,
        yy,
        cloud,
        levels=[0.5, 1.5],
        colors=[AVIATION.cloud_soft],
        alpha=0.42 if professional else 0.34,
        zorder=2,
    )
    _set_hatch_color(cloud_set, AVIATION.cloud, 0.25)
    ax.contour(xx, yy, data.cloud_mask.astype(float), levels=[0.5], colors=[AVIATION.cloud], linewidths=0.55, alpha=0.62, zorder=3)
    if not professional:
        _annotate_zone_runs(
            ax,
            x,
            levels,
            data.cloud_mask,
            symbol="☁",
            label="",
            color=AVIATION.cloud,
            fill="#F8FAFC",
            fallback_y=650.0,
            max_labels=5,
        )


def _draw_icing_layer(ax, data: RouteProfileData, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    icing_mask = data.icing_score > 0
    if not np.any(icing_mask):
        return
    xx, yy = np.meshgrid(x, levels)
    icing = np.ma.masked_where(~icing_mask, data.icing_score)
    hatch = ["..", "...", "...."] if professional else [None, None, None]
    if professional:
        from matplotlib import rc_context

        with rc_context({"hatch.color": AVIATION.icing, "hatch.linewidth": 0.45}):
            icing_set = ax.contourf(
                xx,
                yy,
                icing,
                levels=[0.5, 1.5, 2.5, 3.5],
                colors=[AVIATION.icing_soft] * 3,
                alpha=0.48,
                hatches=hatch,
                zorder=4,
            )
    else:
        icing_set = ax.contourf(
            xx,
            yy,
            icing,
            levels=[0.5, 1.5, 2.5, 3.5],
            colors=[AVIATION.icing_soft] * 3,
            alpha=0.42,
            zorder=4,
        )
    _set_hatch_color(icing_set, AVIATION.icing, 0.42)
    ax.contour(xx, yy, icing_mask.astype(float), levels=[0.5], colors=[AVIATION.icing], linewidths=0.9, zorder=5)
    if not professional:
        _annotate_zone_runs(
            ax,
            x,
            levels,
            icing_mask,
            symbol="❄",
            label="ЛЁД",
            color=AVIATION.icing,
            fill="white",
            fallback_y=600.0,
        )


def _draw_turbulence_layer(ax, data: RouteProfileData, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    turbulence_mask = data.turbulence_score > 0
    if not np.any(turbulence_mask):
        return
    xx, yy = np.meshgrid(x, levels)
    turbulence = np.ma.masked_where(~turbulence_mask, data.turbulence_score)
    hatch = ["/", "//", "///"] if professional else [None, None, None]
    if professional:
        from matplotlib import rc_context

        with rc_context({"hatch.color": AVIATION.turbulence, "hatch.linewidth": 0.45}):
            turbulence_set = ax.contourf(
                xx,
                yy,
                turbulence,
                levels=[0.5, 1.5, 2.5, 3.5],
                colors=[AVIATION.turbulence_soft] * 3,
                alpha=0.38,
                hatches=hatch,
                zorder=4,
            )
    else:
        turbulence_set = ax.contourf(
            xx,
            yy,
            turbulence,
            levels=[0.5, 1.5, 2.5, 3.5],
            colors=[AVIATION.turbulence_soft] * 3,
            alpha=0.30,
            zorder=4,
        )
    _set_hatch_color(turbulence_set, AVIATION.turbulence, 0.48)
    ax.contour(xx, yy, turbulence_mask.astype(float), levels=[0.5], colors=[AVIATION.turbulence], linewidths=0.9, zorder=5)
    if not professional:
        _annotate_zone_runs(
            ax,
            x,
            levels,
            turbulence_mask,
            symbol="≈",
            label="БОЛТ.",
            color=AVIATION.turbulence,
            fill="white",
            fallback_y=820.0,
        )


def _draw_wind_layer(ax, data: RouteProfileData, x: np.ndarray, levels: np.ndarray, *, professional: bool) -> None:
    wind = np.ma.masked_invalid(data.wind_speed_ms)
    if not wind.count():
        return
    xx, yy = np.meshgrid(x, levels)
    strong = np.ma.masked_where(wind < 20.0, wind)
    if strong.count():
        ax.contourf(
            xx,
            yy,
            strong,
            levels=[20.0, 30.0, 40.0, 80.0],
            colors=[AVIATION.wind_soft, "#BDD6F0", "#94B6DE"],
            alpha=0.30 if professional else 0.24,
            zorder=3,
        )

    min_wind = _finite_min(data.wind_speed_ms)
    max_wind = _finite_max(data.wind_speed_ms)
    contour_levels = [level for level in (20.0, 30.0, 40.0) if min_wind <= level <= max_wind]
    if professional and contour_levels:
        contour = ax.contour(
            xx,
            yy,
            data.wind_speed_ms,
            levels=contour_levels,
            colors=[AVIATION.wind] * len(contour_levels),
            linewidths=[0.95, 1.25, 1.65][: len(contour_levels)],
            zorder=7,
        )
        ax.clabel(contour, fmt=lambda value: f"V{value:.0f} м/с", fontsize=5.8, inline=True, inline_spacing=8)

    if not professional:
        strong_point_mask = np.any(data.wind_speed_ms >= 20.0, axis=0)
        for start, end in _mask_runs(strong_point_mask)[:4]:
            center_x = float(np.mean(x[start : end + 1]))
            local = data.wind_speed_ms[:, start : end + 1]
            local_mask = local >= 20.0
            center_y = _zone_pressure_center(local_mask, 0, local_mask.shape[1] - 1, levels, 650.0)
            ax.text(
                center_x,
                center_y,
                f"➤ {format_wind_speed_ms(_finite_max(local))}",
                ha="center",
                va="center",
                fontsize=7.0,
                fontweight="bold",
                color="white",
                zorder=11,
                bbox={"boxstyle": "round,pad=0.22", "fc": AVIATION.wind, "ec": AVIATION.wind_extreme, "lw": 0.8, "alpha": 0.95},
            )


def _draw_professional_guides(ax, data: RouteProfileData, x: np.ndarray, levels: np.ndarray) -> None:
    xx, yy = np.meshgrid(x, levels)
    finite_temp = data.temperature_c[np.isfinite(data.temperature_c)]
    if finite_temp.size:
        available = [level for level in (-20.0, -10.0, 0.0) if float(np.min(finite_temp)) <= level <= float(np.max(finite_temp))]
        for level in available:
            contour = ax.contour(
                xx,
                yy,
                data.temperature_c,
                levels=[level],
                colors=[_ISOTHERM_COLORS[level]],
                linewidths=1.25 if level == 0.0 else 0.95,
                zorder=8,
            )
            ax.clabel(contour, fmt={level: f"{level:.0f}°"}, fontsize=6.2, inline=True, inline_spacing=10)

    finite_rh = data.humidity_pct[np.isfinite(data.humidity_pct)]
    rh_levels = [level for level in (80.0, 90.0) if finite_rh.size and float(np.min(finite_rh)) <= level <= float(np.max(finite_rh))]
    if rh_levels:
        contour = ax.contour(
            xx,
            yy,
            data.humidity_pct,
            levels=rh_levels,
            colors=["#4F86B7"] * len(rh_levels),
            linewidths=0.65,
            linestyles="dashed",
            zorder=6,
        )
        ax.clabel(contour, fmt=lambda value: f"RH {value:.0f}%", fontsize=5.7, inline=True, inline_spacing=10)

    point_step = 1 if len(x) <= 10 else 2
    level_step = 2
    u = data.u_wind_ms[::level_step, ::point_step]
    v = -data.v_wind_ms[::level_step, ::point_step]
    finite = np.isfinite(u) & np.isfinite(v)
    if finite.any():
        ax.barbs(
            x[::point_step],
            levels[::level_step],
            np.where(finite, u, 0.0),
            np.where(finite, v, 0.0),
            length=4.3,
            linewidth=0.42,
            color=AVIATION.wind_extreme,
            alpha=0.75,
            zorder=9,
        )


def _draw_surface_symbols(ax, data: RouteProfileData, x: np.ndarray, *, professional: bool) -> None:
    thunder_mask = np.asarray([point.surface.cb_score >= 2 or point.surface.phenomena == "TSRA" for point in data.waypoints], dtype=bool)
    precip_mask = np.asarray([(point.surface.precip_mm or 0.0) >= 0.2 for point in data.waypoints], dtype=bool) & ~thunder_mask

    for mask, symbol, label, color, y in (
        (thunder_mask, "⚡", "ГРОЗА", AVIATION.convection, 960.0),
        (precip_mask, "●", "ОСАДКИ", "#3F73B8", 975.0),
    ):
        for start, end in _mask_runs(mask)[:4]:
            center_x = float(np.mean(x[start : end + 1]))
            text = f"{symbol} {label}" if professional else symbol
            ax.text(
                center_x,
                y,
                text,
                ha="center",
                va="center",
                fontsize=8.0 if professional else 12.0,
                fontweight="bold",
                color=color,
                zorder=12,
                bbox={"boxstyle": "round,pad=0.20", "fc": "white", "ec": color, "lw": 0.8, "alpha": 0.92},
            )


def _draw_legend_band(ax, image, *, professional: bool) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyBboxPatch, Patch

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(
        FancyBboxPatch(
            (0.002, 0.05),
            0.996,
            0.90,
            transform=ax.transAxes,
            boxstyle="round,pad=0.008,rounding_size=0.02",
            facecolor="#F8FAFC",
            edgecolor="#CAD6E2",
            linewidth=0.8,
        )
    )

    cax = ax.inset_axes([0.018, 0.28, 0.15, 0.42])
    colorbar = ax.figure.colorbar(image, cax=cax, orientation="horizontal")
    colorbar.set_label("фон: температура, °C", fontsize=6.7, labelpad=2)
    colorbar.ax.tick_params(labelsize=6, pad=1)
    colorbar.outline.set_edgecolor("#AAB8C6")
    colorbar.outline.set_linewidth(0.6)

    handles = [
        Patch(facecolor=AVIATION.cloud_soft, edgecolor=AVIATION.cloud, label="☁ облачность"),
        Patch(facecolor=AVIATION.icing_soft, edgecolor=AVIATION.icing, hatch=".." if professional else None, label="❄ обледенение"),
        Patch(facecolor=AVIATION.turbulence_soft, edgecolor=AVIATION.turbulence, hatch="//" if professional else None, label="≈ болтанка"),
        Patch(facecolor=AVIATION.wind_soft, edgecolor=AVIATION.wind, label="➤ сильный ветер"),
        Patch(facecolor=AVIATION.convection_soft, edgecolor=AVIATION.convection, label="⚡ гроза / конвекция"),
    ]
    if professional:
        handles.extend(
            [
                Line2D([0], [0], color="#D32F2F", linewidth=1.3, label="изотермы 0/-10/-20°"),
                Line2D([0], [0], color="#4F86B7", linewidth=0.9, linestyle="--", label="RH 80/90%"),
                Line2D([0], [0], color=AVIATION.wind, linewidth=1.2, label="V20/30/40 м/с + барбы"),
            ]
        )
    ax.legend(
        handles=handles,
        loc="center left",
        bbox_to_anchor=(0.19, 0.50),
        ncol=4 if professional else 5,
        fontsize=7.0,
        frameon=False,
        handlelength=2.6,
        columnspacing=1.25,
    )


def _draw_token_row(ax, group: RouteDisplayGroup, tokens: tuple[HazardToken, ...], *, y: float, fontsize: float) -> None:
    if not tokens:
        ax.text(group.center_km, y, "✓", ha="center", va="center", fontsize=fontsize, color=AVIATION.safe, fontweight="bold")
        return
    width = max(1.0, group.end_km - group.start_km)
    for index, token in enumerate(tokens):
        x_value = group.start_km + width * (index + 1) / (len(tokens) + 1)
        ax.text(x_value, y, token.symbol, ha="center", va="center", fontsize=fontsize, color=token.color, fontweight="bold")


def _draw_route_cards(ax, data: RouteProfileData, *, professional: bool) -> None:
    groups = _display_groups(data, max_cards=_MAX_PRO_CARDS if professional else _MAX_SIMPLE_CARDS)
    ax.set_xlim(0.0, max(1.0, data.total_distance_km))
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")
    ax.set_title("Профессиональная сводка участков" if professional else "Понятная оценка участков", loc="left", fontsize=9.3, fontweight="bold", pad=6)

    for group in groups:
        score = _group_risk(data, group)
        tokens = _hazard_tokens_for_indices(data, group.point_indices, limit=4 if professional else 3)
        start_point = data.waypoints[group.start_index]
        width = max(1.0, group.end_km - group.start_km)
        ax.barh(
            0.5,
            width,
            left=group.start_km,
            height=0.94,
            color=risk_color(score, soft=True),
            edgecolor="#FFFFFF",
            linewidth=1.0,
            align="center",
        )
        ax.text(group.center_km, 0.88, f"{group.start_km:.0f}–{group.end_km:.0f} км", ha="center", va="center", fontsize=6.5, color=METEO.muted_text)
        status = risk_label(score)
        if status == "ВЫСОКИЙ РИСК":
            status = "ВЫСОКИЙ\nРИСК"
        ax.text(
            group.center_km,
            0.71 if professional else 0.67,
            status,
            ha="center",
            va="center",
            fontsize=7.2 if professional else 8.0,
            fontweight="bold",
            color=risk_color(score),
            linespacing=0.9,
        )
        _draw_token_row(ax, group, tokens, y=0.50 if professional else 0.42, fontsize=9.5 if professional else 12.0)

        if professional:
            indices = group.point_indices
            vmax = _finite_max(data.wind_speed_ms[:, indices])
            vis_values = [data.waypoints[index].surface.visibility_km for index in indices if data.waypoints[index].surface.visibility_km is not None]
            ceiling_values = [data.waypoints[index].surface.ceiling_m for index in indices if data.waypoints[index].surface.ceiling_m is not None]
            precip = max((data.waypoints[index].surface.precip_mm or 0.0) for index in indices)
            vis = min(vis_values) if vis_values else None
            ceiling = min(ceiling_values) if ceiling_values else None
            line1 = f"Vmax {vmax:.0f} м/с · P {precip:.1f} мм"
            line2 = f"VIS {'—' if vis is None else f'{vis:.0f} км'} · ВНГО {'—' if ceiling is None else f'{ceiling:.0f} м'}"
            ax.text(group.center_km, 0.30, line1 + "\n" + line2, ha="center", va="center", fontsize=5.5, color=METEO.muted_text, linespacing=1.12)
            time_y = 0.08
        else:
            summary = " · ".join(token.label for token in tokens[:2]) if tokens else "значимых рисков нет"
            ax.text(group.center_km, 0.25, summary, ha="center", va="center", fontsize=5.9, color=METEO.muted_text)
            time_y = 0.09
        ax.text(group.center_km, time_y, f"+{start_point.lead_hour} ч · {start_point.valid_time_utc:%d.%m %H}Z", ha="center", va="center", fontsize=6.0, color=METEO.axis_text)

    ax.text(0.5, -0.08, "Расстояние по маршруту · срок и UTC-время в начале участка", transform=ax.transAxes, ha="center", va="top", fontsize=8.0, color=METEO.axis_text)


def _set_axis_ticks(ax, data: RouteProfileData, levels: np.ndarray, x: np.ndarray, *, professional: bool) -> None:
    selected = _PRO_Y_LEVELS if professional else _SIMPLE_Y_LEVELS
    available = [level for level in selected if level in set(int(value) for value in levels)]
    labels: list[str] = []
    ticks: list[float] = []
    for level in available:
        row_index = int(np.where(levels == level)[0][0])
        finite = data.height_m[row_index, :][np.isfinite(data.height_m[row_index, :])]
        height = float(np.nanmean(finite) / 1000.0) if finite.size else math.nan
        labels.append(f"{level}\n{height:.1f} км" if math.isfinite(height) else f"{level}\n—")
        ticks.append(float(level))
    ax.set_yticks(ticks)
    ax.set_yticklabels(labels, fontsize=7.3)
    ax.set_ylabel("Давление, гПа / средняя высота")

    if len(x) <= 12:
        tick_indices = list(range(len(x)))
    else:
        tick_indices = sorted(set(np.linspace(0, len(x) - 1, 10).round().astype(int).tolist()))
    ax.set_xticks(x[tick_indices])
    ax.set_xticklabels([f"{x[index]:.0f} км" for index in tick_indices], fontsize=7.0)
    ax.tick_params(axis="x", labelbottom=True)


def write_route_profile_png(data: RouteProfileData) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    apply_meteo_rcparams(plt)
    x = _x_values(data)
    levels = np.asarray(data.levels_hpa, dtype=float)
    professional = data.mode == "pro"
    tmp = tempfile.NamedTemporaryFile(prefix="gfs_route_profile", suffix=_safe_suffix(data), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        fig_width = max(14.0, min(22.0, 10.0 + len(x) * 0.58))
        fig_height = 10.4 if professional else 8.8
        fig = plt.figure(figsize=(fig_width, fig_height), facecolor=METEO.figure_bg)
        grid = fig.add_gridspec(
            3,
            1,
            height_ratios=[5.5, 0.72, 1.95 if professional else 1.55],
            left=0.065,
            right=0.985,
            top=0.855,
            bottom=0.075,
            hspace=0.18,
        )
        ax = fig.add_subplot(grid[0])
        legend_ax = fig.add_subplot(grid[1])
        cards_ax = fig.add_subplot(grid[2])

        image = _draw_temperature_background(ax, data, x, levels, professional=professional)
        _draw_cloud_layer(ax, data, x, levels, professional=professional)
        _draw_wind_layer(ax, data, x, levels, professional=professional)
        _draw_icing_layer(ax, data, x, levels, professional=professional)
        _draw_turbulence_layer(ax, data, x, levels, professional=professional)
        if professional:
            _draw_professional_guides(ax, data, x, levels)
        _draw_surface_symbols(ax, data, x, professional=professional)

        ax.set_ylim(float(np.max(levels)) + 15.0, float(np.min(levels)) - 15.0)
        ax.set_xlim(0.0, max(1.0, data.total_distance_km))
        _set_axis_ticks(ax, data, levels, x, professional=professional)
        style_axis(ax, grid=True)
        ax.set_title(
            "Профессиональный вертикальный разрез: T / RH / ветер / риски" if professional else "Простой разрез: где облака и опасные зоны",
            loc="left",
            fontsize=10.2,
            fontweight="bold",
            pad=8,
        )

        origin = _short_label(data.origin.label)
        destination = _short_label(data.destination.label)
        mode_label = "ПРОФЕССИОНАЛЬНЫЙ" if professional else "ПРОСТОЙ"
        fig.text(0.025, 0.966, f"Маршрут: {origin} → {destination}", ha="left", va="top", fontsize=17.0, fontweight="bold", color=METEO.axis_text)
        fig.text(
            0.025,
            0.925,
            f"Расстояние: {data.total_distance_km:.0f} км   |   скорость: {data.speed_kmh} км/ч   |   "
            f"вылет: +{data.departure_lead} ч   |   GFS run: {data.run.date} {data.run.cycle}Z",
            ha="left",
            va="top",
            fontsize=9.0,
            color=METEO.axis_text,
        )
        fig.text(
            0.975,
            0.958,
            f"РЕЖИМ: {mode_label}",
            ha="right",
            va="top",
            fontsize=9.0,
            color="white",
            fontweight="bold",
            bbox={"boxstyle": "round,pad=0.52", "fc": AVIATION.route, "ec": AVIATION.route, "alpha": 0.98},
        )

        _draw_legend_band(legend_ax, image, professional=professional)
        _draw_route_cards(cards_ax, data, professional=professional)

        add_footer(
            fig,
            "GFS grid, не радиозонд. Обледенение и болтанка — диагностические прокси по T/RH и вертикальному сдвигу ветра. "
            "Не является разрешением на полёт; обязательны METAR/TAF/SIGMET/GAMET/NOTAM и решение командира.",
            y=0.012,
        )
        fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=METEO.figure_bg)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
