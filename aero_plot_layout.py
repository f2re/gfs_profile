from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np

import aero_plot as base
from gfs_core import ProfileResult
from plot_style import METEO, add_footer, apply_meteo_rcparams, style_axis

FIGURE_SIZE = (16.4, 10.2)
BARB_XLOC = 0.982
BARB_MAX_COUNT = 18

# Fixed figure coordinates. Every block has a real gutter around it; no title or
# label is expected to occupy the neighbouring panel.
AERO_LAYOUT: dict[str, tuple[float, float, float, float]] = {
    "main": (0.055, 0.105, 0.590, 0.755),
    "curve_legend": (0.055, 0.885, 0.590, 0.035),
    "cards": (0.675, 0.625, 0.305, 0.265),
    "hazards": (0.675, 0.365, 0.145, 0.190),
    "wind": (0.835, 0.365, 0.145, 0.190),
    "hodograph": (0.675, 0.105, 0.145, 0.190),
    "layer_legend": (0.835, 0.105, 0.145, 0.190),
}

HODOGRAPH_LABEL_OFFSETS: dict[int, tuple[int, int]] = {
    0: (7, 10),
    1: (8, -13),
    3: (8, 9),
    6: (-32, 10),
    8: (-32, -14),
}


def rectangles_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return not (
        ax + aw <= bx
        or bx + bw <= ax
        or ay + ah <= by
        or by + bh <= ay
    )


def _barb_indices(df, max_count: int = BARB_MAX_COUNT) -> np.ndarray:
    pressure = df["pressure_hpa"].to_numpy(dtype=float)
    valid = np.flatnonzero(np.isfinite(pressure) & (pressure >= 100.0) & (pressure <= 1000.0))
    if len(valid) <= max_count:
        return valid
    positions = np.rint(np.linspace(0, len(valid) - 1, max_count)).astype(int)
    return np.unique(valid[positions])


def _curve_legend(ax, handles, labels) -> None:
    ax.axis("off")
    desired = (
        "Температура среды",
        "Точка росы",
        "Кривая частицы",
        "Насыщение надо льдом",
    )
    by_label = {label: handle for handle, label in zip(handles, labels)}
    ordered_labels = [label for label in desired if label in by_label]
    ordered_handles = [by_label[label] for label in ordered_labels]
    if not ordered_handles:
        return
    ax.legend(
        ordered_handles,
        ordered_labels,
        loc="center left",
        bbox_to_anchor=(0.0, 0.0, 1.0, 1.0),
        mode="expand",
        ncol=len(ordered_handles),
        fontsize=7.4,
        frameon=False,
        handlelength=2.7,
        columnspacing=1.25,
        borderaxespad=0,
    )


def _layer_legend(ax) -> None:
    from matplotlib.patches import Patch

    ax.axis("off")
    handles = [
        Patch(facecolor=base.HAZARD_SOFT["cloud"], edgecolor=base.HAZARD_COLORS["cloud"], label="Облачность"),
        Patch(facecolor=base.HAZARD_SOFT["icing"], edgecolor=base.HAZARD_COLORS["icing"], label="Обледенение"),
        Patch(facecolor=base.HAZARD_SOFT["turb"], edgecolor=base.HAZARD_COLORS["turb"], label="Болтанка"),
        Patch(facecolor=base.HAZARD_SOFT["conv"], edgecolor=base.HAZARD_COLORS["conv"], label="Конв. слой"),
        Patch(facecolor=base.HAZARD_SOFT["precip"], edgecolor=base.HAZARD_COLORS["precip"], label="Осадки"),
    ]
    ax.text(
        0.0,
        0.98,
        "Условные обозначения",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9.0,
        fontweight="bold",
        color=METEO.axis_text,
    )
    ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.0, 0.84),
        fontsize=6.8,
        frameon=False,
        handlelength=2.4,
        labelspacing=0.62,
        borderaxespad=0,
    )
    ax.text(
        0.0,
        0.02,
        "Диагностика по влажности,\nтемпературе и сдвигу ветра.",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=6.25,
        linespacing=1.15,
        color=METEO.muted_text,
    )


def _hodograph(ax, df) -> None:
    from matplotlib.collections import LineCollection

    profile = df.sort_values("geopotential_height_m")
    profile = profile[profile["geopotential_height_m"] <= 8000].dropna(
        subset=["u_wind_ms", "v_wind_ms"]
    )
    if len(profile) >= 2:
        u = profile["u_wind_ms"].to_numpy(dtype=float)
        v = profile["v_wind_ms"].to_numpy(dtype=float)
        height = profile["geopotential_height_km"].to_numpy(dtype=float)
        points = np.column_stack([u, v])
        segments = np.stack([points[:-1], points[1:]], axis=1)
        collection = LineCollection(segments, cmap="viridis", linewidth=2.2, alpha=0.92)
        collection.set_array((height[:-1] + height[1:]) / 2.0)
        ax.add_collection(collection)
        ax.scatter(
            u,
            v,
            c=height,
            cmap="viridis",
            s=17,
            edgecolor="white",
            linewidth=0.35,
            zorder=4,
        )
        for km, offset in HODOGRAPH_LABEL_OFFSETS.items():
            row = profile.loc[(profile["geopotential_height_km"] - km).abs().idxmin()]
            ax.annotate(
                f"{km} км",
                (row["u_wind_ms"], row["v_wind_ms"]),
                xytext=offset,
                textcoords="offset points",
                fontsize=6.5,
                fontweight="bold",
                ha="left" if offset[0] >= 0 else "right",
                va="bottom" if offset[1] >= 0 else "top",
                bbox={"boxstyle": "round,pad=0.11", "fc": "white", "ec": "none", "alpha": 0.86},
                arrowprops={"arrowstyle": "-", "color": METEO.annotation_edge, "lw": 0.45},
                annotation_clip=True,
                zorder=6,
            )
        limit = max(8.0, float(np.nanmax(np.abs(np.concatenate([u, v])))) + 4.0)
        limit = math.ceil(limit / 5.0) * 5.0
        ax.set_xlim(-limit, limit)
        ax.set_ylim(-limit, limit)
    else:
        ax.text(0.5, 0.5, "Годограф недоступен", ha="center", va="center", transform=ax.transAxes)
    ax.axhline(0, color=METEO.grid_minor, linewidth=0.7)
    ax.axvline(0, color=METEO.grid_minor, linewidth=0.7)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Годограф 0–8 км", fontsize=9.0, fontweight="bold", pad=5)
    ax.set_xlabel("u, м/с", fontsize=7.2, labelpad=2)
    ax.set_ylabel("v, м/с", fontsize=7.2, labelpad=2)
    ax.tick_params(labelsize=6.7, pad=2)
    style_axis(ax)


def _plot_metpy_diagram(result: ProfileResult, out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from metpy.units import units

    apply_meteo_rcparams(plt)
    df = base._prepare_profile(result)
    diag = base._metpy_diagnostics(df)
    layers = base._diagnose_layers(df)
    p = df["pressure_hpa"].to_numpy(dtype=float) * units.hPa
    t = df["temperature_c"].to_numpy(dtype=float) * units.degC
    td = df["dewpoint_c"].to_numpy(dtype=float) * units.degC
    u = df["u_wind_ms"].to_numpy(dtype=float) * units("m/s")
    v = df["v_wind_ms"].to_numpy(dtype=float) * units("m/s")
    frost = base._frost_point_curve(df)

    fig = None
    try:
        fig = plt.figure(figsize=FIGURE_SIZE, facecolor=METEO.figure_bg)
        diagram = base._create_metpy_diagram(fig)
        diagram.ax.set_position(AERO_LAYOUT["main"])
        diagram.ax.set_facecolor("#FAFCFE")

        base._draw_layer_spans(diagram.ax, layers)
        diagram.plot(p, t, linewidth=2.8, color=base.MAIN_CURVE_COLORS["temperature"], label="Температура среды", zorder=9)
        diagram.plot(p, td, linewidth=2.35, color=base.MAIN_CURVE_COLORS["dewpoint"], label="Точка росы", zorder=8)
        if np.isfinite(frost).any():
            diagram.plot(
                p,
                frost * units.degC,
                linewidth=1.4,
                color=base.MAIN_CURVE_COLORS["ice_saturation"],
                linestyle="--",
                label="Насыщение надо льдом",
                zorder=7,
            )
        if diag.get("parcel") is not None:
            diagram.plot(
                p,
                diag["parcel"],
                linewidth=2.05,
                color=base.MAIN_CURVE_COLORS["parcel"],
                label="Кривая частицы",
                zorder=10,
            )
            try:
                diagram.shade_cape(p, t, diag["parcel"], alpha=0.14, color="#F59E0B")
                diagram.shade_cin(p, t, diag["parcel"], alpha=0.10, color="#718096")
            except Exception:
                pass

        barb_idx = _barb_indices(df)
        if len(barb_idx):
            diagram.plot_barbs(
                p[barb_idx],
                u[barb_idx],
                v[barb_idx],
                xloc=BARB_XLOC,
                length=5.0,
                color="#203B63",
                linewidth=0.62,
            )
        diagram.plot_dry_adiabats(linewidth=0.45, alpha=0.28, color="#C5A46B")
        diagram.plot_moist_adiabats(linewidth=0.48, alpha=0.30, color="#3A9B8A")
        diagram.plot_mixing_lines(linewidth=0.38, alpha=0.26, color="#6F9B77")

        tmin = min(-72.0, float(df[["temperature_c", "dewpoint_c"]].min().min()) - 8.0)
        tmax = max(36.0, float(df["temperature_c"].max()) + 8.0)
        diagram.ax.set_ylim(1050, 100)
        diagram.ax.set_xlim(tmin, tmax)
        base._add_isotherm_guides(diagram.ax, df, tmin, tmax)
        base._draw_reference_levels(diagram.ax, df, diag)
        style_axis(diagram.ax)
        diagram.ax.set_xlabel("Температура, °C", labelpad=3)
        diagram.ax.set_ylabel("Давление, гПа", labelpad=4)

        title, subtitle = base._diagram_title(result)
        fig.text(0.055, 0.978, title, ha="left", va="top", fontsize=15.0, fontweight="bold", color=METEO.axis_text)
        fig.text(0.055, 0.948, subtitle, ha="left", va="top", fontsize=8.7, color=METEO.muted_text)

        handles, labels = diagram.ax.get_legend_handles_labels()
        curve_legend = fig.add_axes(AERO_LAYOUT["curve_legend"])
        _curve_legend(curve_legend, handles, labels)

        cards = fig.add_axes(AERO_LAYOUT["cards"])
        base._plot_index_cards(cards, df, diag, layers)

        hazards = fig.add_axes(AERO_LAYOUT["hazards"], sharey=diagram.ax)
        base._hazards(hazards, layers)
        hazards.set_title("Слои по высоте", fontsize=9.0, fontweight="bold", pad=5)
        hazards.tick_params(axis="x", labelsize=6.4, pad=2)

        wind = fig.add_axes(AERO_LAYOUT["wind"], sharey=diagram.ax)
        base._wind_panel(wind, df)
        wind.set_title("Ветер и сдвиг", fontsize=9.0, fontweight="bold", pad=5)
        wind.set_xlabel("м/с · м/с/км", fontsize=7.1, labelpad=3)
        wind.tick_params(axis="x", labelsize=6.7, pad=2)

        hodograph = fig.add_axes(AERO_LAYOUT["hodograph"])
        _hodograph(hodograph, df)

        layer_legend = fig.add_axes(AERO_LAYOUT["layer_legend"])
        _layer_legend(layer_legend)

        add_footer(
            fig,
            "GFS grid, не радиозонд. Облачность, обледенение и болтанка — диагностические модельные слои.",
            y=0.022,
        )
        # Keep the exact canvas geometry. tight bbox reflows outside artists and
        # was the main reason that independently positioned panels collided.
        fig.savefig(out_path, dpi=180, facecolor=METEO.figure_bg, pad_inches=0)
    finally:
        if fig is not None:
            plt.close(fig)


def write_aero_png(result: ProfileResult, diagram_type: str = base.DEFAULT_AERO_DIAGRAM) -> Path:
    normalized = str(diagram_type or base.DEFAULT_AERO_DIAGRAM).lower().strip()
    if normalized != base.DEFAULT_AERO_DIAGRAM:
        raise ValueError("Доступна одна аэрологическая диаграмма: Skew-T log-P")
    tmp = tempfile.NamedTemporaryFile(
        prefix="gfs_aero",
        suffix=base._safe_suffix(result),
        delete=False,
    )
    out_path = Path(tmp.name)
    tmp.close()
    try:
        _plot_metpy_diagram(result, out_path)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path
