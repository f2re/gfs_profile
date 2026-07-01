from __future__ import annotations

import math
import tempfile
from pathlib import Path

from gfs_core import ProfileResult
from plot_style import METEO, add_footer, annotation_box_kwargs, apply_meteo_rcparams, style_axis

SUPPORTED_AERO_DIAGRAMS = {"stuve", "emagram", "skewt"}
AERO_LEVELS_HPA: tuple[int, ...] | None = None
ISOTHERM_TARGETS_C = (0.0, -10.0, -20.0)
DIAGRAM_RU_NAMES = {
    "stuve": "Stüve",
    "emagram": "Эмаграмма",
    "skewt": "Skew-T log-P",
}
ISOTHERM_LINE_COLORS = {
    0.0: METEO.freezing,
    -10.0: METEO.minus10,
    -20.0: METEO.minus20,
}


def _safe_suffix(result: ProfileResult, diagram_type: str) -> str:
    suffix = f"_{diagram_type}_{result.run.date}_{result.run.cycle}_f{result.lead_hour:03d}_{result.grid_lat:.3f}_{result.grid_lon:.3f}.png"
    return suffix.replace("-", "m").replace(" ", "_")


def _diagram_title(result: ProfileResult, diagram_type: str) -> str:
    diagram_name = DIAGRAM_RU_NAMES.get(diagram_type, diagram_type.upper())
    return (
        f"GFS 0.25 · аэрологическая диаграмма {diagram_name} · запуск {result.run.date} {result.run.cycle}Z · "
        f"срок +{result.lead_hour} ч · действительно {result.valid_time_utc:%Y-%m-%d %H:%M UTC}\n"
        f"узел GFS {result.grid_lat:.2f}, {result.grid_lon:.2f}; модельный профиль, не радиозонд"
    )


def _prepare_profile(result: ProfileResult):
    df = result.dataframe.dropna(subset=["pressure_hpa", "temperature_c", "dewpoint_c", "u_wind_ms", "v_wind_ms"]).copy()
    df = df.sort_values("pressure_hpa", ascending=False)
    if df.empty:
        raise ValueError("Пустой профиль: нечего строить")
    return df


def _interpolate_isotherm_height(df, target_c: float) -> float | None:
    prof = df.sort_values("geopotential_height_m")[["temperature_c", "geopotential_height_m"]].dropna()
    if prof.empty:
        return None
    temps = prof["temperature_c"].to_numpy(dtype=float)
    heights = prof["geopotential_height_m"].to_numpy(dtype=float)
    for idx in range(len(temps) - 1):
        t0 = temps[idx]
        t1 = temps[idx + 1]
        if math.isclose(t0, target_c, abs_tol=0.05):
            return float(heights[idx])
        if (t0 >= target_c >= t1) or (t0 <= target_c <= t1):
            if math.isclose(t0, t1, abs_tol=1e-9):
                return float(heights[idx])
            ratio = (target_c - t0) / (t1 - t0)
            return float(heights[idx] + ratio * (heights[idx + 1] - heights[idx]))
    return None


def _diagnostic_box_text(result: ProfileResult, df) -> str:
    lines = [
        "Диагностика GFS",
        f"lead +{result.lead_hour} ч",
        f"узел {result.grid_lat:.2f}, {result.grid_lon:.2f}",
    ]
    isotherms: list[str] = []
    for target in ISOTHERM_TARGETS_C:
        height = _interpolate_isotherm_height(df, target)
        if height is None:
            isotherms.append("—")
        else:
            isotherms.append(f"{height / 1000.0:.1f}")
    lines.append("H 0/-10/-20°C: " + "/".join(isotherms) + " км")
    if "wind_speed_ms" in df:
        max_wind = float(df["wind_speed_ms"].max())
        lines.append(f"Vmax: {max_wind:.0f} м/с")
    lines.append("Барбы справа: U/V GFS, м/с")
    return "\n".join(lines)


def _create_metpy_diagram(fig, diagram_type: str):
    from metpy.plots import Emagram, SkewT, Stuve

    if diagram_type == "skewt":
        return SkewT(fig, rotation=30)
    if diagram_type == "emagram":
        return Emagram(fig)
    return Stuve(fig)


def _add_isotherm_guides(axis, temp_min: float, temp_max: float) -> None:
    for target in ISOTHERM_TARGETS_C:
        if temp_min <= target <= temp_max:
            axis.axvline(
                target,
                color=ISOTHERM_LINE_COLORS[target],
                linewidth=1.0 if target == 0 else 0.8,
                linestyle="-" if target == 0 else "--",
                alpha=0.82,
                zorder=1,
            )
            axis.text(
                target,
                1035,
                f"{int(target)}°C",
                ha="center",
                va="bottom",
                fontsize=7,
                color=ISOTHERM_LINE_COLORS[target],
                bbox={"facecolor": METEO.axes_bg, "edgecolor": "none", "alpha": 0.75, "pad": 1.4},
            )


def _plot_metpy_diagram(result: ProfileResult, diagram_type: str, out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from metpy.units import units

    apply_meteo_rcparams(plt)

    df = _prepare_profile(result)

    pressure = df["pressure_hpa"].to_numpy(dtype=float) * units.hPa
    temperature = df["temperature_c"].to_numpy(dtype=float) * units.degC
    dewpoint = df["dewpoint_c"].to_numpy(dtype=float) * units.degC
    u_wind = df["u_wind_ms"].to_numpy(dtype=float) * units("m/s")
    v_wind = df["v_wind_ms"].to_numpy(dtype=float) * units("m/s")

    fig = None
    try:
        fig = plt.figure(figsize=(10.5, 10.2), facecolor=METEO.figure_bg)
        diagram = _create_metpy_diagram(fig, diagram_type)
        diagram.ax.set_facecolor(METEO.axes_bg)

        diagram.plot(pressure, temperature, linewidth=2.5, color=METEO.temperature, label="T — температура", zorder=6)
        diagram.plot(pressure, dewpoint, linewidth=2.25, color=METEO.dewpoint, label="Td — точка росы", zorder=6)
        diagram.plot_barbs(pressure, u_wind, v_wind, xloc=1.045, color=METEO.wind, linewidth=0.75)
        diagram.plot_dry_adiabats(linewidth=0.55, alpha=0.55, color="#C7A569")
        diagram.plot_moist_adiabats(linewidth=0.60, alpha=0.55, color="#3BA99C")
        diagram.plot_mixing_lines(linewidth=0.45, alpha=0.46, color="#74A57F")

        temp_min = min(-70.0, float(df[["temperature_c", "dewpoint_c"]].min().min()) - 8.0)
        temp_max = max(35.0, float(df["temperature_c"].max()) + 8.0)
        diagram.ax.set_ylim(1050, 100)
        diagram.ax.set_xlim(temp_min, temp_max)
        _add_isotherm_guides(diagram.ax, temp_min, temp_max)

        style_axis(diagram.ax)
        diagram.ax.set_title(_diagram_title(result, diagram_type), fontsize=10.5, fontweight="bold", color=METEO.axis_text, pad=14)
        diagram.ax.set_xlabel("Температура T, °C")
        diagram.ax.set_ylabel("Давление p, гПа")
        diagram.ax.legend(loc="upper right", fontsize=8, framealpha=0.94)
        diagram.ax.text(
            0.022,
            0.025,
            _diagnostic_box_text(result, df),
            transform=diagram.ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=8,
            color=METEO.axis_text,
            bbox=annotation_box_kwargs(),
        )
        add_footer(fig, "Сухие/влажные адиабаты и линии отношения смеси — справочная термодинамическая сетка MetPy. Данные: модельная точка GFS.")
        fig.tight_layout(rect=(0, 0.04, 1, 0.98))
        fig.savefig(out_path, dpi=180, bbox_inches="tight")
    finally:
        if fig is not None:
            plt.close(fig)


def write_aero_png(result: ProfileResult, diagram_type: str = "stuve") -> Path:
    """Render a MetPy aerological diagram and return a temporary PNG path."""

    diagram_type = diagram_type.lower().strip()
    if diagram_type not in SUPPORTED_AERO_DIAGRAMS:
        raise ValueError(f"Неподдерживаемый тип аэродиаграммы: {diagram_type}")

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_aero", suffix=_safe_suffix(result, diagram_type), delete=False)
    out_path = Path(tmp.name)
    tmp.close()
    try:
        _plot_metpy_diagram(result, diagram_type, out_path)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path
