from __future__ import annotations

from pathlib import Path

from aero_plot import AERO_LEVELS_HPA, DEFAULT_AERO_DIAGRAM
from aero_plot_layout import write_aero_png
from gfs_core import GfsRun, ProfileResult, ProgressCallback
from gfs_product_core import build_profile_for_levels


def build_aero_product(
    run: GfsRun,
    lead_hour: int,
    lat: float,
    lon: float,
    diagram_type: str = DEFAULT_AERO_DIAGRAM,
    progress_callback: ProgressCallback | None = None,
) -> tuple[ProfileResult, Path]:
    """Build the single GFS Skew-T log-P product with hodograph."""

    result = build_profile_for_levels(
        run,
        lead_hour,
        lat,
        lon,
        levels_hpa=AERO_LEVELS_HPA,
        progress_callback=progress_callback,
    )
    if progress_callback:
        progress_callback({"stage": "plot_start", "message": "Строю аэрологическую диаграмму"})
    png_path = write_aero_png(result, diagram_type=DEFAULT_AERO_DIAGRAM)
    if progress_callback:
        progress_callback({"stage": "plot_done", "message": "Аэрологическая диаграмма готова", "file": str(png_path)})
    return result, png_path


def format_aero_caption(result: ProfileResult, diagram_type: str = DEFAULT_AERO_DIAGRAM) -> str:
    return (
        "🧾 GFS · аэрологическая диаграмма\n"
        f"{result.run.date} {result.run.cycle}Z · +{result.lead_hour} ч · {result.valid_time_utc:%d.%m %H:%M UTC}\n"
        f"Узел {result.grid_lat:.3f}, {result.grid_lon:.3f}"
    )
