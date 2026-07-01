from __future__ import annotations

import tempfile
from pathlib import Path

from gfs_core import ProfileResult

SUPPORTED_AERO_DIAGRAMS = {"stuve", "emagram", "skewt"}
AERO_LEVELS_HPA: tuple[int, ...] | None = None


def _safe_suffix(result: ProfileResult, diagram_type: str) -> str:
    suffix = f"_{diagram_type}_{result.run.date}_{result.run.cycle}_f{result.lead_hour:03d}_{result.grid_lat:.3f}_{result.grid_lon:.3f}.png"
    return suffix.replace("-", "m").replace(" ", "_")


def _diagram_title(result: ProfileResult, diagram_type: str) -> str:
    return (
        f"GFS 0.25 {diagram_type.upper()} | запуск {result.run.date} {result.run.cycle}Z | "
        f"срок +{result.lead_hour} ч | действительно {result.valid_time_utc:%Y-%m-%d %H:%M UTC}\n"
        f"узел GFS {result.grid_lat:.2f}, {result.grid_lon:.2f} | модельная точка, не радиозонд"
    )


def _prepare_profile(result: ProfileResult):
    df = result.dataframe.dropna(subset=["pressure_hpa", "temperature_c", "dewpoint_c", "u_wind_ms", "v_wind_ms"]).copy()
    df = df.sort_values("pressure_hpa", ascending=False)
    if df.empty:
        raise ValueError("Пустой профиль: нечего строить")
    return df


def _create_metpy_diagram(fig, diagram_type: str):
    from metpy.plots import Emagram, SkewT, Stuve

    if diagram_type == "skewt":
        return SkewT(fig, rotation=30)
    if diagram_type == "emagram":
        return Emagram(fig)
    return Stuve(fig)


def _plot_metpy_diagram(result: ProfileResult, diagram_type: str, out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from metpy.units import units

    df = _prepare_profile(result)

    pressure = df["pressure_hpa"].to_numpy(dtype=float) * units.hPa
    temperature = df["temperature_c"].to_numpy(dtype=float) * units.degC
    dewpoint = df["dewpoint_c"].to_numpy(dtype=float) * units.degC
    u_wind = df["u_wind_ms"].to_numpy(dtype=float) * units("m/s")
    v_wind = df["v_wind_ms"].to_numpy(dtype=float) * units("m/s")

    fig = None
    try:
        fig = plt.figure(figsize=(9, 9))
        diagram = _create_metpy_diagram(fig, diagram_type)
        diagram.plot(pressure, temperature, linewidth=2, label="T")
        diagram.plot(pressure, dewpoint, linewidth=2, label="Td")
        diagram.plot_barbs(pressure, u_wind, v_wind, xloc=1.02)
        diagram.plot_dry_adiabats(linewidth=0.5, alpha=0.55)
        diagram.plot_moist_adiabats(linewidth=0.5, alpha=0.55)
        diagram.plot_mixing_lines(linewidth=0.45, alpha=0.45)

        diagram.ax.set_ylim(1050, 100)
        diagram.ax.set_title(_diagram_title(result, diagram_type), fontsize=10)
        diagram.ax.set_xlabel("Температура, °C")
        diagram.ax.set_ylabel("Давление, гПа")
        diagram.ax.grid(True, which="both", linewidth=0.4)
        diagram.ax.legend(loc="best", fontsize=8)
        fig.tight_layout()
        fig.savefig(out_path, dpi=160, bbox_inches="tight")
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
