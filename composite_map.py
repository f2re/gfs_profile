from __future__ import annotations

"""Public facade for the GFS composite map.

I/O/grid helpers and rendering live in dedicated modules. The meteorological
builder is imported lazily to avoid a circular import and is the only builder
implementation used by Telegram, tests and smoke checks.
"""

from geocode import GeoPoint
from gfs_core import GfsRun, ProgressCallback
from composite_map_io import (
    MAP_BASEMAP_BASIC,
    MAP_BASEMAP_DEFAULT,
    MAP_BASEMAP_PLACES,
    MAP_BASEMAP_ROADS,
    MAP_BASEMAP_WATER,
    MAP_BASEMAPS,
    MAP_LEVEL_TOKENS,
    MAP_MAX_ANIMATION_FRAMES,
    MAP_MAX_PNG_SERIES_FRAMES,
    MAP_RADIUS_KM,
    MAP_RING_STEP_KM,
    MAP_VARIABLES,
    _area_subset_url,
    _coords_from_dataarray,
    _lon180,
    _lon_delta,
    _validate_basemap,
    _xy_km,
    _xy_point,
    area_box_from_radius,
    download_area_subset,
)
from composite_map_render import (
    weather_code_icon,
    write_composite_map_gif,
    write_composite_map_png,
)


def build_composite_map(
    run: GfsRun,
    lead_hour: int,
    point: GeoPoint,
    radius_km: float = MAP_RADIUS_KM,
    basemap: str = MAP_BASEMAP_DEFAULT,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    from composite_map_meteorology import build_composite_map as audited_builder

    return audited_builder(
        run,
        lead_hour,
        point,
        radius_km=radius_km,
        basemap=basemap,
        progress_callback=progress_callback,
    )


def build_composite_map_frames(
    run: GfsRun,
    leads: list[int],
    point: GeoPoint,
    radius_km: float = MAP_RADIUS_KM,
    basemap: str = MAP_BASEMAP_DEFAULT,
    progress_callback: ProgressCallback | None = None,
) -> list[dict]:
    from composite_map_meteorology import build_composite_map_frames as audited_builder

    return audited_builder(
        run,
        leads,
        point,
        radius_km=radius_km,
        basemap=basemap,
        progress_callback=progress_callback,
    )


__all__ = [
    "MAP_BASEMAP_BASIC",
    "MAP_BASEMAP_DEFAULT",
    "MAP_BASEMAP_PLACES",
    "MAP_BASEMAP_ROADS",
    "MAP_BASEMAP_WATER",
    "MAP_BASEMAPS",
    "MAP_LEVEL_TOKENS",
    "MAP_MAX_ANIMATION_FRAMES",
    "MAP_MAX_PNG_SERIES_FRAMES",
    "MAP_RADIUS_KM",
    "MAP_RING_STEP_KM",
    "MAP_VARIABLES",
    "area_box_from_radius",
    "build_composite_map",
    "build_composite_map_frames",
    "download_area_subset",
    "weather_code_icon",
    "write_composite_map_gif",
    "write_composite_map_png",
]
