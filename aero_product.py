from __future__ import annotations

from pathlib import Path

from aero_plot import AERO_LEVELS_HPA, SUPPORTED_AERO_DIAGRAMS, write_aero_png
from gfs_core import GfsProfileError, GfsRun, ProfileResult, ProgressCallback
from gfs_product_core import build_profile_for_levels


def build_aero_product(
    run: GfsRun,
    lead_hour: int,
    lat: float,
    lon: float,
    diagram_type: str = "stuve",
    progress_callback: ProgressCallback | None = None,
) -> tuple[ProfileResult, Path]:
    """Build a GFS model aerological diagram product."""

    diagram_type = diagram_type.lower().strip()
    if diagram_type not in SUPPORTED_AERO_DIAGRAMS:
        raise GfsProfileError(f"Неподдерживаемый тип аэродиаграммы: {diagram_type}")

    result = build_profile_for_levels(
        run,
        lead_hour,
        lat,
        lon,
        levels_hpa=AERO_LEVELS_HPA,
        progress_callback=progress_callback,
    )
    if progress_callback:
        progress_callback({"stage": "plot_start", "message": "Строю аэрологическую диаграмму", "diagram_type": diagram_type})
    png_path = write_aero_png(result, diagram_type=diagram_type)
    if progress_callback:
        progress_callback({"stage": "plot_done", "message": "Аэрологическая диаграмма построена", "file": str(png_path)})
    return result, png_path


def format_aero_caption(result: ProfileResult, diagram_type: str) -> str:
    return (
        f"🧾 GFS 0.25 {diagram_type.upper()}\n"
        f"{result.run.date} {result.run.cycle}Z +{result.lead_hour}ч → {result.valid_time_utc:%d.%m %H:%M UTC}\n"
        f"⊞ {result.grid_lat:.3f},{result.grid_lon:.3f}\n"
        "Модельная аэрологическая диаграмма, не радиозонд."
    )
