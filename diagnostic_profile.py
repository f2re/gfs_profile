from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from gfs_core import (
    DEFAULT_PROFILE_LEVELS_HPA,
    GfsRun,
    ProfileResult,
    ProgressCallback,
    add_derived_parameters,
    extract_profile_from_grib_file,
)
from gfs_subset import download_gfs_subset_to_disk, open_grib_datasets, scalar_from_datasets


RD = 287.05
RV = 461.5
G = 9.80665
KAPPA = 0.2854

DIAGNOSTIC_PROFILE_VARIABLES = (
    "TMP",
    "RH",
    "UGRD",
    "VGRD",
    "HGT",
    "CLWMR",
    "ICMR",
    "RWMR",
    "SNMR",
    "GRLE",
    "PRES",
    "DPT",
)
MICROPHYSICS_COLUMNS = {
    "clwmr": "cloud_liquid_mixing_ratio_kgkg",
    "icmr": "cloud_ice_mixing_ratio_kgkg",
    "rwmr": "rain_mixing_ratio_kgkg",
    "snmr": "snow_mixing_ratio_kgkg",
    "grle": "graupel_mixing_ratio_kgkg",
}


def pressure_level_tokens(levels_hpa: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(f"lev_{int(level)}_mb" for level in levels_hpa)


def _sat_vapor_pressure_hpa(temperature_c: np.ndarray | float) -> np.ndarray:
    values = np.asarray(temperature_c, dtype=float)
    return 6.112 * np.exp((17.67 * values) / (values + 243.5))


def relative_humidity_from_t_td(temperature_c: float, dewpoint_c: float) -> float:
    es = float(_sat_vapor_pressure_hpa(temperature_c))
    e = float(_sat_vapor_pressure_hpa(dewpoint_c))
    return float(np.clip(100.0 * e / max(es, 1e-6), 0.0, 100.0))


def air_density_kg_m3(pressure_hpa, temperature_k, relative_humidity_pct) -> np.ndarray:
    """Moist-air density from dry-air and water-vapour partial pressures."""

    pressure = np.asarray(pressure_hpa, dtype=float)
    temperature = np.asarray(temperature_k, dtype=float)
    rh = np.clip(np.asarray(relative_humidity_pct, dtype=float), 0.0, 100.0) / 100.0
    temp_c = temperature - 273.15
    vapour_hpa = rh * _sat_vapor_pressure_hpa(temp_c)
    dry_hpa = np.maximum(0.0, pressure - vapour_hpa)
    with np.errstate(divide="ignore", invalid="ignore"):
        density = dry_hpa * 100.0 / (RD * temperature) + vapour_hpa * 100.0 / (RV * temperature)
    density[~np.isfinite(density)] = np.nan
    return density


def icing_proxy_score(
    temperature_c: float,
    supercooled_liquid_water_gm3: float | None,
    relative_humidity_pct: float,
    *,
    microphysics_available: bool,
) -> int:
    """Local icing-potential scale, not an operational intensity category.

    With GFS liquid hydrometeors the score is based on supercooled liquid-water
    content. A T/RH-only fallback is capped at 1 because RH alone overestimates
    icing extent.
    """

    temperature = float(temperature_c)
    if not -30.0 <= temperature <= 0.5:
        return 0

    if microphysics_available and supercooled_liquid_water_gm3 is not None:
        water = max(0.0, float(supercooled_liquid_water_gm3))
        if water >= 0.20 and temperature >= -20.0:
            return 3
        if water >= 0.05 and temperature >= -25.0:
            return 2
        if water >= 0.005:
            return 1
        return 0

    if -20.0 <= temperature <= 0.0 and float(relative_humidity_pct) >= 90.0:
        return 1
    return 0


def turbulence_proxy_score(shear_ms_per_km: float, gradient_richardson: float | None) -> int:
    """Conservative CAT proxy using resolved shear and gradient Ri.

    This is not EDR/GTG. High categories require both strong resolved shear and
    dynamically weak stability; coarse GFS layers cannot resolve all turbulence.
    """

    shear = max(0.0, float(shear_ms_per_km))
    ri = float(gradient_richardson) if gradient_richardson is not None else math.nan
    if shear >= 15.0 and math.isfinite(ri) and ri < 0.25:
        return 3
    if shear >= 15.0 or (shear >= 10.0 and math.isfinite(ri) and ri < 0.5):
        return 2
    if shear >= 6.0 or (shear >= 4.0 and math.isfinite(ri) and ri < 1.0):
        return 1
    return 0


def _thetae_bolton(frame: pd.DataFrame) -> np.ndarray:
    p = frame["pressure_hpa"].to_numpy(dtype=float)
    t = frame["temperature_k"].to_numpy(dtype=float)
    td_c = frame["dewpoint_c"].to_numpy(dtype=float)
    td = td_c + 273.15
    e = _sat_vapor_pressure_hpa(td_c)
    r = 0.622 * e / np.maximum(p - e, 0.1)
    theta = t * np.power(1000.0 / p, KAPPA)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        tl = 1.0 / (1.0 / np.maximum(td - 56.0, 1.0) + np.log(np.maximum(t / td, 1e-6)) / 800.0) + 56.0
        thetae = theta * np.exp((3376.0 / tl - 2.54) * r * (1.0 + 0.81 * r))
    thetae[~np.isfinite(thetae)] = np.nan
    return thetae


def add_profile_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    """Add dimensioned microphysics and transparent proxy scores."""

    out = frame.copy().sort_values("geopotential_height_m").reset_index(drop=True)
    for column in MICROPHYSICS_COLUMNS.values():
        if column not in out:
            out[column] = np.nan

    density = air_density_kg_m3(
        out["pressure_hpa"].to_numpy(dtype=float),
        out["temperature_k"].to_numpy(dtype=float),
        out["relative_humidity_pct"].to_numpy(dtype=float),
    )
    liquid_q = (
        out["cloud_liquid_mixing_ratio_kgkg"].fillna(0.0).to_numpy(dtype=float)
        + out["rain_mixing_ratio_kgkg"].fillna(0.0).to_numpy(dtype=float)
    )
    ice_q = (
        out["cloud_ice_mixing_ratio_kgkg"].fillna(0.0).to_numpy(dtype=float)
        + out["snow_mixing_ratio_kgkg"].fillna(0.0).to_numpy(dtype=float)
        + out["graupel_mixing_ratio_kgkg"].fillna(0.0).to_numpy(dtype=float)
    )
    micro_available = out[list(MICROPHYSICS_COLUMNS.values())].notna().any(axis=1).to_numpy(dtype=bool)
    slwc = np.where(out["temperature_c"].to_numpy(dtype=float) <= 0.5, density * liquid_q * 1000.0, 0.0)
    iwc = density * ice_q * 1000.0
    total_condensate = np.maximum(0.0, slwc) + np.maximum(0.0, iwc)

    out["air_density_kg_m3"] = density
    out["supercooled_liquid_water_content_gm3"] = slwc
    out["ice_water_content_gm3"] = iwc
    out["total_condensate_gm3"] = total_condensate
    out["microphysics_available"] = micro_available

    spread = out["temperature_c"].to_numpy(dtype=float) - out["dewpoint_c"].to_numpy(dtype=float)
    rh = out["relative_humidity_pct"].to_numpy(dtype=float)
    fallback_cloud = (rh >= 90.0) | ((rh >= 80.0) & (spread <= 2.5))
    out["cloud_proxy"] = np.where(micro_available, total_condensate >= 0.001, fallback_cloud)
    out["cloud_proxy_source"] = np.where(micro_available, "GFS hydrometeor mixing ratios", "T/RH fallback")

    out["icing_proxy_score"] = [
        icing_proxy_score(t, w, h, microphysics_available=available)
        for t, w, h, available in zip(
            out["temperature_c"].to_numpy(dtype=float),
            slwc,
            rh,
            micro_available,
        )
    ]
    out["icing_proxy_source"] = np.where(micro_available, "GFS SLWC proxy", "T/RH fallback; max score 1")

    z = out["geopotential_height_m"].to_numpy(dtype=float)
    u = out["u_wind_ms"].to_numpy(dtype=float)
    v = out["v_wind_ms"].to_numpy(dtype=float)
    theta = out["temperature_k"].to_numpy(dtype=float) * np.power(1000.0 / out["pressure_hpa"].to_numpy(dtype=float), KAPPA)
    if len(out) >= 3 and len(np.unique(z)) >= 3:
        with np.errstate(divide="ignore", invalid="ignore"):
            du_dz = np.gradient(u, z, edge_order=1)
            dv_dz = np.gradient(v, z, edge_order=1)
            dtheta_dz = np.gradient(theta, z, edge_order=1)
            shear_s = np.sqrt(du_dz**2 + dv_dz**2)
            shear_km = shear_s * 1000.0
            denominator = du_dz**2 + dv_dz**2
            ri = np.where(denominator >= 1e-8, (G / np.maximum(theta, 1.0) * dtheta_dz) / denominator, np.nan)
    else:
        shear_km = np.full(len(out), np.nan)
        ri = np.full(len(out), np.nan)
    out["vertical_shear_ms_per_km"] = shear_km
    out["gradient_richardson"] = ri
    out["turbulence_proxy_score"] = [
        turbulence_proxy_score(shear, value if np.isfinite(value) else None)
        for shear, value in zip(shear_km, ri)
    ]
    out["thetae_k"] = _thetae_bolton(out)
    if len(out) >= 3 and len(np.unique(z)) >= 3:
        out["thetae_lapse_k_per_km"] = np.gradient(out["thetae_k"].to_numpy(dtype=float), z / 1000.0, edge_order=1)
    else:
        out["thetae_lapse_k_per_km"] = np.nan
    return out.sort_values("pressure_hpa", ascending=False).reset_index(drop=True)


def _microphysics_for_levels(datasets: list[object], levels_hpa: tuple[int, ...]) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for level in levels_hpa:
        row: dict[str, float] = {"pressure_hpa": float(level)}
        for short_name, column in MICROPHYSICS_COLUMNS.items():
            value = scalar_from_datasets(
                datasets,
                (short_name,),
                type_of_level=("isobaricInhPa",),
                level=float(level),
                step_types=("instant",),
            )
            row[column] = np.nan if value is None else max(0.0, float(value))
        rows.append(row)
    return pd.DataFrame(rows)


def _surface_row(datasets: list[object]) -> dict[str, float | str] | None:
    pressure_pa = scalar_from_datasets(datasets, ("sp", "pres"), type_of_level=("surface",), step_types=("instant",))
    height_gpm = scalar_from_datasets(datasets, ("gh", "hgt"), type_of_level=("surface",), step_types=("instant",))
    temperature_k = scalar_from_datasets(
        datasets,
        ("t2m", "2t", "t", "tmp"),
        type_of_level=("heightAboveGround",),
        level=2.0,
        step_types=("instant",),
    )
    dewpoint_k = scalar_from_datasets(
        datasets,
        ("d2m", "2d", "dpt", "dewpoint"),
        type_of_level=("heightAboveGround",),
        level=2.0,
        step_types=("instant",),
    )
    rh = scalar_from_datasets(
        datasets,
        ("r2", "2r", "r", "rh"),
        type_of_level=("heightAboveGround",),
        level=2.0,
        step_types=("instant",),
    )
    u = scalar_from_datasets(
        datasets,
        ("u10", "10u", "u", "ugrd"),
        type_of_level=("heightAboveGround",),
        level=10.0,
        step_types=("instant",),
    )
    v = scalar_from_datasets(
        datasets,
        ("v10", "10v", "v", "vgrd"),
        type_of_level=("heightAboveGround",),
        level=10.0,
        step_types=("instant",),
    )
    if pressure_pa is None or height_gpm is None or temperature_k is None:
        return None
    temperature_c = float(temperature_k) - 273.15
    if dewpoint_k is not None:
        dewpoint_c = float(dewpoint_k) - 273.15
        rh_value = relative_humidity_from_t_td(temperature_c, dewpoint_c)
    elif rh is not None:
        rh_value = float(np.clip(rh, 1.0, 100.0))
        alpha = math.log(rh_value / 100.0) + 17.625 * temperature_c / (243.04 + temperature_c)
        dewpoint_c = 243.04 * alpha / (17.625 - alpha)
    else:
        return None
    return {
        "pressure_hpa": float(pressure_pa) / 100.0,
        "temperature_k": float(temperature_k),
        "relative_humidity_pct": rh_value,
        "u_wind_ms": float(u or 0.0),
        "v_wind_ms": float(v or 0.0),
        "geopotential_height_m": float(height_gpm),
        "level_source": "GFS surface/2 m/10 m",
        "dewpoint_c": dewpoint_c,
    }


def build_diagnostic_profile(
    run: GfsRun,
    lead_hour: int,
    lat: float,
    lon: float,
    *,
    levels_hpa: tuple[int, ...] | None = None,
    include_surface_row: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> ProfileResult:
    """Build one GFS point subset with thermodynamics and hydrometeors."""

    selected_levels = tuple(levels_hpa or DEFAULT_PROFILE_LEVELS_HPA)
    level_tokens = list(pressure_level_tokens(selected_levels))
    if include_surface_row:
        level_tokens.extend(("lev_surface", "lev_2_m_above_ground", "lev_10_m_above_ground"))

    path, grid_lat, grid_lon = download_gfs_subset_to_disk(
        run.date,
        run.cycle,
        lead_hour,
        lat,
        lon,
        DIAGNOSTIC_PROFILE_VARIABLES,
        tuple(level_tokens),
        product_key="diagnostic_profile_surface" if include_surface_row else "diagnostic_profile",
        progress_callback=progress_callback,
    )

    frame = extract_profile_from_grib_file(path, progress_callback=progress_callback)
    with tempfile.TemporaryDirectory() as tmp:
        datasets = open_grib_datasets(path, Path(tmp))
        microphysics = _microphysics_for_levels(datasets, selected_levels)
        frame = frame.merge(microphysics, on="pressure_hpa", how="left")
        if include_surface_row:
            surface = _surface_row(datasets)
            if surface is not None:
                surface_height = float(surface["geopotential_height_m"])
                surface_pressure = float(surface["pressure_hpa"])
                frame = frame[
                    (frame["geopotential_height_m"] >= surface_height - 20.0)
                    & (frame["pressure_hpa"] <= surface_pressure + 5.0)
                ].copy()
                row = {column: np.nan for column in frame.columns}
                row.update(surface)
                frame = pd.concat([pd.DataFrame([row]), frame], ignore_index=True)

    frame = add_derived_parameters(frame)
    if "level_source" not in frame:
        frame["level_source"] = "GFS isobaric"
    else:
        frame["level_source"] = frame["level_source"].fillna("GFS isobaric")
    frame = add_profile_diagnostics(frame)

    if progress_callback:
        progress_callback({"stage": "done", "message": "Диагностический профиль готов", "rows": len(frame)})
    return ProfileResult(
        run=run,
        lead_hour=lead_hour,
        requested_lat=lat,
        requested_lon=lon,
        grid_lat=grid_lat,
        grid_lon=grid_lon,
        grib_path=path,
        dataframe=frame,
    )
