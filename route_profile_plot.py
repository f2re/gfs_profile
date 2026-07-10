from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np

from aviation_style import AVIATION, risk_color, risk_label
from plot_style import METEO, add_footer, apply_meteo_rcparams, style_axis, temperature_cmap_and_norm
from route_profile import RouteProfileData


def _safe_suffix(data: RouteProfileData) -> str:
    value = f"_{data.mode}_{data.run.date}_{data.run.cycle}_f{data.departure_lead:03d}_{data.speed_kmh}kmh.png"
    return value.replace("-", "m").replace(" ", "_")


def _x_values(data: RouteProfileData) -> np.ndarray:
    return np.asarray([point.distance_km for point in data.waypoints], dtype=float)


def _x_labels(data: RouteProfileData) -> list[str]:
    labels: list[str] = []
    for point in data.waypoints:
        labels.append(f"{point.distance_km:.0f} км\n+{point.lead_hour}ч\n{point.valid_time_utc:%d.%m %H}Z")
    return labels


def _mean_height_labels(data: RouteProfileData) -> list[str]:
    labels: list[str] = []
    for row_index, level in enumerate(data.levels_hpa):
        values = data.height_m[row_index, :]
        finite = values[np.isfinite(values)]
        mean_km = float(np.nanmean(finite) / 1000.0) if finite.size else math.nan
        labels.append(f"{level}\n{mean_km:.1f} км" if math.isfinite(mean_km) else f"{level}\n—")
    return labels


def _draw_risk_strip(ax, data: RouteProfileData, *, show_details: bool) -> None:
    x = _x_values(data)
    if len(x) == 1:
        edges = np.array([x[0] - 0.5, x[0] + 0.5])
    else:
        mids = (x[:-1] + x[1:]) / 2.0
        edges = np.concatenate(([x[0] - (mids[0] - x[0])], mids, [x[-1] + (x[-1] - mids[-1])]))
    for index, point in enumerate(data.waypoints):
        ax.axvspan(edges[index], edges[index + 1], color=risk_color(point.risk_score, soft=True), alpha=1.0, ec="#FFFFFF", lw=1.0)
        ax.text(point.distance_km, 0.80 if show_details else 0.62, risk_label(point.risk_score, short=True), ha="center", va="center", fontsize=7.5, fontweight="bold", color=risk_color(point.risk_score))
        if show_details:
            phenomena = point.surface.phenomena if point.surface.phenomena != "—" else "без явлений"
            vis = "—" if point.surface.visibility_km is None else f"VIS {point.surface.visibility_km:.0f}км"
            ceil = "—" if point.surface.ceiling_m is None else f"ВНГО {point.surface.ceiling_m:.0f}м"
            low = "—" if point.surface.low_cloud_pct is None else f"{point.surface.low_cloud_pct:.0f}"
            mid = "—" if point.surface.mid_cloud_pct is None else f"{point.surface.mid_cloud_pct:.0f}"
            high = "—" if point.surface.high_cloud_pct is None else f"{point.surface.high_cloud_pct:.0f}"
            precip = "—" if point.surface.precip_mm is None else f"{point.surface.precip_mm:.1f}мм"
            cape = "—" if point.surface.cape_jkg is None else f"{point.surface.cape_jkg:.0f}"
            details = f"{phenomena} · P {precip}\nCLD L/M/H {low}/{mid}/{high}%\n{vis} · {ceil}\nCAPE {cape} · CB {point.surface.cb_score}"
            ax.text(point.distance_km, 0.34, details, ha="center", va="center", fontsize=5.8, linespacing=1.05, color=METEO.muted_text)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xlim(edges[0], edges[-1])
    ax.set_xlabel("Расстояние по маршруту / расчётный срок GFS")
    ax.set_title("Модельная оценка участков маршрута", loc="left", fontsize=9, fontweight="bold")
    for spine in ax.spines.values():
        spine.set_visible(False)


def _draw_overlays(ax, data: RouteProfileData, x: np.ndarray, y: np.ndarray, *, professional: bool) -> None:
    xx, yy = np.meshgrid(x, y)
    if np.any(data.cloud_mask):
        cloud = np.ma.masked_where(~data.cloud_mask, np.ones_like(data.temperature_c))
        ax.pcolormesh(xx, yy, cloud, cmap="Blues", vmin=0, vmax=2.6, shading="nearest", alpha=0.22)

    if np.any(data.icing_score > 0):
        icing = np.ma.masked_where(data.icing_score <= 0, data.icing_score)
        ax.contourf(xx, yy, icing, levels=[0.5, 1.5, 2.5, 3.5], colors=[AVIATION.icing] * 3, alpha=0.12, hatches=["//", "///", "////"])
    if np.any(data.turbulence_score > 0):
        turbulence = np.ma.masked_where(data.turbulence_score <= 0, data.turbulence_score)
        ax.contourf(xx, yy, turbulence, levels=[0.5, 1.5, 2.5, 3.5], colors=[AVIATION.turbulence] * 3, alpha=0.10, hatches=["xx", "xxx", "xxxx"])

    if professional:
        finite_temp = np.isfinite(data.temperature_c)
        if finite_temp.any():
            for level, color in ((0.0, METEO.freezing), (-10.0, METEO.minus10), (-20.0, METEO.minus20)):
                if float(np.nanmin(data.temperature_c)) <= level <= float(np.nanmax(data.temperature_c)):
                    contour = ax.contour(xx, yy, data.temperature_c, levels=[level], colors=[color], linewidths=0.9)
                    ax.clabel(contour, fmt={level: f"{level:.0f}°"}, fontsize=6, inline=True)
        finite_rh = data.humidity_pct[np.isfinite(data.humidity_pct)]
        rh_levels = [level for level in (80.0, 90.0) if finite_rh.size and float(np.min(finite_rh)) <= level <= float(np.max(finite_rh))]
        if rh_levels:
            rh_contour = ax.contour(xx, yy, data.humidity_pct, levels=rh_levels, colors=[METEO.humidity], linewidths=0.55, linestyles="dotted")
            ax.clabel(rh_contour, fmt=lambda value: f"RH{value:.0f}", fontsize=5.6, inline=True)
        finite_wind = data.wind_speed_ms[np.isfinite(data.wind_speed_ms)]
        wind_levels = [level for level in (10.0, 20.0, 30.0) if finite_wind.size and float(np.min(finite_wind)) <= level <= float(np.max(finite_wind))]
        if wind_levels:
            wind_contour = ax.contour(xx, yy, data.wind_speed_ms, levels=wind_levels, colors=[AVIATION.wind], linewidths=0.55, linestyles="dashed")
            ax.clabel(wind_contour, fmt=lambda value: f"V{value:.0f}", fontsize=5.6, inline=True)

        step = 1 if len(x) <= 10 else 2
        level_step = 2
        u = data.u_wind_ms[::level_step, ::step]
        v = -data.v_wind_ms[::level_step, ::step]
        finite = np.isfinite(u) & np.isfinite(v)
        if finite.any():
            u = np.where(finite, u, 0.0)
            v = np.where(finite, v, 0.0)
            ax.barbs(x[::step], y[::level_step], u, v, length=4.6, linewidth=0.45, color=AVIATION.wind, alpha=0.75)


def write_route_profile_png(data: RouteProfileData) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    apply_meteo_rcparams(plt)
    x = _x_values(data)
    y = np.asarray(data.levels_hpa, dtype=float)
    cmap, norm = temperature_cmap_and_norm()
    tmp = tempfile.NamedTemporaryFile(prefix="gfs_route_profile", suffix=_safe_suffix(data), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        professional = data.mode == "pro"
        fig_width = max(12.5, min(21.0, 8.0 + len(x) * 0.55))
        fig_height = 11.0 if professional else 8.6
        fig = plt.figure(figsize=(fig_width, fig_height), facecolor=METEO.figure_bg)
        grid = fig.add_gridspec(2, 1, height_ratios=[5.2, 1.75 if professional else 1.45], hspace=0.16)
        ax = fig.add_subplot(grid[0])
        strip = fig.add_subplot(grid[1], sharex=ax)

        image = ax.pcolormesh(x, y, data.temperature_c, cmap=cmap, norm=norm, shading="nearest")
        _draw_overlays(ax, data, x, y, professional=professional)
        ax.set_ylim(max(y) + 15, min(y) - 15)
        ax.set_yticks(y)
        ax.set_yticklabels(_mean_height_labels(data), fontsize=7.5)
        ax.set_ylabel("p, гПа / средняя геопотенциальная высота")
        ax.set_xticks(x)
        ax.set_xticklabels(_x_labels(data), fontsize=7, rotation=0)
        ax.tick_params(axis="x", labelbottom=False)
        style_axis(ax, grid=True)

        title_mode = "профессиональный" if professional else "простой"
        ax.set_title(
            f"GFS 0.25 · вертикальный профиль по маршруту до 500 гПа · {title_mode}\n"
            f"{data.origin.label} → {data.destination.label} · {data.total_distance_km:.0f} км · {data.speed_kmh} км/ч · "
            f"вылет +{data.departure_lead} ч · run {data.run.date} {data.run.cycle}Z",
            fontsize=11,
            fontweight="bold",
            pad=12,
        )

        colorbar = fig.colorbar(image, ax=ax, pad=0.012, fraction=0.025)
        colorbar.set_label("Температура, °C")
        colorbar.ax.tick_params(labelsize=8)

        legend_items = [
            Patch(facecolor=AVIATION.cloud_soft, edgecolor=AVIATION.cloud, label="облачный слой RH ≥80%"),
            Patch(facecolor="none", edgecolor=AVIATION.icing, hatch="///", label="модельный риск обледенения"),
            Patch(facecolor="none", edgecolor=AVIATION.turbulence, hatch="xxx", label="сдвиг ветра / турбулентность"),
        ]
        if professional:
            legend_items.append(Patch(facecolor=AVIATION.wind, edgecolor=AVIATION.wind, label="ветер: барбы и изолинии V10/20/30 м/с"))
        ax.legend(handles=legend_items, loc="upper right", fontsize=7.5, ncol=2 if professional else 1)

        _draw_risk_strip(strip, data, show_details=professional)
        strip.set_xticks(x)
        strip.set_xticklabels(_x_labels(data), fontsize=7)

        if not professional:
            for point in data.waypoints:
                labels: list[str] = []
                if point.surface.cb_score >= 2:
                    labels.append("⚡")
                if point.surface.phenomena in {"RA", "TSRA", "FZRA", "SN"}:
                    labels.append(point.surface.phenomena)
                if np.nanmax(data.icing_score[:, point.index]) >= 1:
                    labels.append("ICE")
                if np.nanmax(data.turbulence_score[:, point.index]) >= 1:
                    labels.append("TURB")
                if labels:
                    ax.text(point.distance_km, 520, " · ".join(labels), ha="center", va="bottom", fontsize=7.2, fontweight="bold", color=risk_color(point.risk_score), bbox={"boxstyle": "round,pad=0.22", "fc": "white", "ec": risk_color(point.risk_score), "alpha": 0.88})

        add_footer(
            fig,
            "GFS grid, не радиозонд. Риск обледенения и турбулентности — диагностические прокси по T/RH и вертикальному сдвигу ветра. "
            "Не использовать как разрешение на полёт; обязательны METAR/TAF/SIGMET/NOTAM и решение командира.",
            y=0.012,
        )
        fig.tight_layout(rect=(0.01, 0.055, 0.99, 0.99))
        fig.savefig(out_path, dpi=180, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
