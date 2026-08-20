from __future__ import annotations

"""Live NOMADS smoke for the audited meteorological field contracts.

This script intentionally performs network I/O. Unit tests use synthetic xarray
fixtures; this smoke verifies that current GFS/NOMADS inventory exposes the
shortNames, levels and step metadata expected by the implementation.
"""

import numpy as np

from diagnostic_profile import build_diagnostic_profile
from cloudgram_product import _read_cloudgram_cell
from composite_map_meteorology import build_composite_map
from geocode import GeoPoint
from gfs_core import build_profile, latest_available_run_for_lead

POINT = GeoPoint(45.0355, 38.9753, "Краснодар", "smoke")
PROFILE_LEVELS = (1000, 925, 850, 700, 500)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    run24 = latest_available_run_for_lead(24)
    print(f"GFS live smoke: run {run24.date} {run24.cycle}Z +24")

    diagnostic = build_diagnostic_profile(
        run24,
        24,
        POINT.lat,
        POINT.lon,
        levels_hpa=PROFILE_LEVELS,
        include_surface_row=True,
    )
    frame = diagnostic.dataframe
    required_columns = {
        "cloud_liquid_mixing_ratio_kgkg",
        "cloud_ice_mixing_ratio_kgkg",
        "rain_mixing_ratio_kgkg",
        "snow_mixing_ratio_kgkg",
        "graupel_mixing_ratio_kgkg",
        "supercooled_liquid_water_content_gm3",
        "icing_proxy_score",
        "gradient_richardson",
        "turbulence_proxy_score",
    }
    _require(required_columns.issubset(frame.columns), f"Нет диагностических колонок: {sorted(required_columns - set(frame.columns))}")
    _require(bool(frame["microphysics_available"].any()), "Изобарические гидрометеоры GFS не прочитаны")
    _require(bool(frame["liquid_microphysics_available"].any()), "CLWMR/RWMR GFS не прочитаны")
    _require("GFS surface/2 m/10 m" in set(frame["level_source"].astype(str)), "Surface/2 m/10 m строка Aero не построена")
    print(f"diagnostic profile: {len(frame)} rows, grid {diagnostic.grid_lat:.3f},{diagnostic.grid_lon:.3f}")

    cell, grid_lat, grid_lon, missing = _read_cloudgram_cell(
        run24,
        24,
        POINT.lat,
        POINT.lon,
        duration_hours=3,
    )
    _require(cell.cape_layer == "180–0 hPa AGL", f"CAPE/CIN слой не подтверждён: {cell.cape_layer}")
    _require(cell.visibility_km is not None, "VIS surface не прочитан")
    _require(cell.precip_interval_hours is not None and cell.precip_interval_hours > 0, "Интервал APCP/PRATE не определён")
    print(
        "cloudgram cell: "
        f"grid {grid_lat:.3f},{grid_lon:.3f}, CAPE layer={cell.cape_layer}, "
        f"VIS={cell.visibility_km:.2f} km, ceiling_AGL={cell.ceiling_m}, missing={sorted(missing)}"
    )

    map_data = build_composite_map(run24, 24, POINT, radius_km=25.0, basemap="basic")
    _require(map_data["cape_layer"] == "180–0 hPa AGL", f"Map CAPE/CIN слой не подтверждён: {map_data['cape_layer']}")
    _require(map_data["visibility"] is not None, "Map VIS surface не прочитан")
    _require(map_data["u500"] is not None and map_data["v500"] is not None, "Map U/V 500 hPa не прочитаны")
    _require(map_data["precip_interval_hours"] > 0, "Map precipitation interval отсутствует")
    _require(map_data["precip_rate_mmh"] is not None, "Map PRATE forecast не прочитан: нельзя достоверно ставить значки текущих осадков")
    _require(map_data["phenomenon_code"] is not None, "Map phenomenon grid отсутствует")
    _require(np.asarray(map_data["phenomenon_code"]).shape == np.asarray(map_data["x"]).shape, "Map phenomenon grid не совпадает с GFS grid")
    print(
        "map fields: "
        f"shape={map_data['x'].shape}, precip={map_data['precip_source']} "
        f"Δt={map_data['precip_interval_hours']:g} h, rate={map_data['precip_rate_source']}, "
        f"phenomena={map_data['phenomenon_source']}, missing={sorted(map_data['missing'])}"
    )

    run384 = latest_available_run_for_lead(384)
    long_profile = build_profile(run384, 384, 55.75, 37.62)
    _require(not long_profile.dataframe.empty, "GFS +384 профиль пуст")
    print(f"GFS +384: run {run384.date} {run384.cycle}Z, rows={len(long_profile.dataframe)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
