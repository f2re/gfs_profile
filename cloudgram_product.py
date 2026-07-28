from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from gfs_core import GfsProfileError, GfsRun, ProgressCallback, canonical_leads
from gfs_subset import (
    GribFieldSelector,
    bool_from_datasets,
    download_gfs_subset_to_disk,
    open_grib_datasets,
    scalar_from_datasets,
    select_grib_field,
)
from weather_diagnostics import precipitation_code, thunder_score, visibility_km as diagnostic_visibility_km, weather_code


CLOUDGRAM_DEFAULT_TO = 72
CLOUDGRAM_DEFAULT_STEP = 3
CLOUDGRAM_MAX_TO = 120
CAPE_LAYER_PA = 18000.0

CLOUDGRAM_VARIABLES = (
    "LCDC",
    "MCDC",
    "HCDC",
    "TCDC",
    "HGT",
    "APCP",
    "PRATE",
    "ACPCP",
    "CPRAT",
    "CRAIN",
    "CSNOW",
    "CFRZR",
    "CICEP",
    "CAPE",
    "CIN",
    "VIS",
)
CLOUDGRAM_LEVEL_TOKENS = (
    "lev_low_cloud_layer",
    "lev_middle_cloud_layer",
    "lev_high_cloud_layer",
    "lev_entire_atmosphere",
    "lev_cloud_ceiling",
    "lev_surface",
    "lev_convective_cloud_layer",
    "lev_180-0_mb_above_ground",
)


@dataclass(frozen=True)
class CloudgramCell:
    lead_hour: int
    valid_time_utc: datetime
    high_cloud_pct: float | None
    mid_cloud_pct: float | None
    low_cloud_pct: float | None
    total_cloud_pct: float | None
    ceiling_m: float | None
    precip_mm: float | None
    precip_rate_mmh: float | None
    conv_precip_mm: float | None
    precip_type: str
    cape_jkg: float | None
    cin_jkg: float | None
    cb_score: int
    visibility_km: float | None
    phenomena: str
    hazard_score: int
    hazard_text: str
    ceiling_msl_gpm: float | None = None
    surface_elevation_gpm: float | None = None
    convective_cloud_pct: float | None = None
    conv_precip_rate_mmh: float | None = None
    cape_layer: str = "180–0 hPa AGL"
    precip_interval_hours: float | None = None
    conv_precip_interval_hours: float | None = None


@dataclass(frozen=True)
class CloudgramData:
    run: GfsRun
    requested_lat: float
    requested_lon: float
    grid_lat: float
    grid_lon: float
    leads: list[int]
    cells: list[CloudgramCell]
    missing_fields: tuple[str, ...] = ()


def cloudgram_leads(lead_from: int = 0, lead_to: int = CLOUDGRAM_DEFAULT_TO, step: int = CLOUDGRAM_DEFAULT_STEP) -> list[int]:
    if step <= 0:
        raise GfsProfileError("Шаг cloudgram должен быть положительным")
    if lead_from < 0 or lead_to < lead_from:
        raise GfsProfileError("Некорректный диапазон сроков cloudgram")
    if lead_to > CLOUDGRAM_MAX_TO:
        raise GfsProfileError(f"cloudgram ограничен +{CLOUDGRAM_MAX_TO} ч")
    allowed = set(canonical_leads())
    leads = [lead for lead in range(lead_from, lead_to + 1, step) if lead in allowed]
    if not leads:
        raise GfsProfileError("В диапазоне cloudgram нет допустимых сроков GFS")
    return leads


def _clip_pct(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return max(0.0, min(100.0, float(value)))


def _mm_from_kgm2(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return max(0.0, float(value))


def _mmh_from_prate(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return max(0.0, float(value) * 3600.0)


def _precip_from_total_or_rate(total_mm: float | None, rate_mmh: float | None, duration_hours: float) -> float | None:
    if total_mm is not None:
        return total_mm
    if rate_mmh is None:
        return None
    return max(0.0, float(rate_mmh) * max(1.0, float(duration_hours)))


def _visibility_km(value: float | None) -> float | None:
    return diagnostic_visibility_km(value)


def _precip_type_from_flags(rain: bool, snow: bool, freezing: bool, ice_pellets: bool) -> str:
    return precipitation_code(rain, snow, freezing, ice_pellets)


def _cb_score(
    cape: float | None,
    cin: float | None,
    conv_precip_mm: float | None,
    conv_cloud_pct: float | None,
    conv_precip_rate_mmh: float | None,
    conv_precip_interval_hours: float = 1.0,
) -> int:
    return thunder_score(
        cape,
        cin,
        conv_precip_mm,
        conv_cloud_pct,
        conv_precip_rate_mmh,
        conv_precip_interval_hours=conv_precip_interval_hours,
    )


def _phenomena(precip_mm: float | None, precip_type: str, cb_score: int, visibility_km: float | None) -> str:
    return weather_code(precip_mm, precip_type, cb_score, visibility_km)


def _hazard_score(
    cb_score: int,
    precip_mm: float | None,
    ceiling_m: float | None,
    visibility_km: float | None,
    phenomena: str,
    precip_interval_hours: float = 1.0,
) -> tuple[int, str]:
    score = 0
    reasons: list[str] = []
    interval = max(1e-6, float(precip_interval_hours or 1.0))
    precip_equivalent_rate = None if precip_mm is None else max(0.0, float(precip_mm)) / interval

    if precip_equivalent_rate is not None and precip_equivalent_rate >= 0.2:
        score = max(score, 1)
        reasons.append("осадки")
    if precip_equivalent_rate is not None and precip_equivalent_rate >= 3.0:
        score = max(score, 2)
        reasons.append("сильные осадки")

    if ceiling_m is not None and ceiling_m < 1000:
        score = max(score, 2)
        reasons.append("низкий ВНГО AGL")
    if ceiling_m is not None and ceiling_m < 300:
        score = max(score, 3)
        reasons.append("очень низкий ВНГО AGL")

    if visibility_km is not None and visibility_km < 5:
        score = max(score, 2)
        reasons.append("ухудшение видимости")
    if visibility_km is not None and visibility_km < 1:
        score = max(score, 3)
        reasons.append("плохая видимость")

    if cb_score >= 2:
        score = max(score, 2)
        reasons.append("конвективный потенциал")
    if phenomena == "TSRA":
        score = max(score, 4)
        reasons.append("модельная гроза")

    return score, ", ".join(dict.fromkeys(reasons)) if reasons else "спокойно"


def _instant_or_average(datasets, names: tuple[str, ...], type_of_level: tuple[str, ...], duration_hours: float) -> float | None:
    value = scalar_from_datasets(datasets, names, type_of_level=type_of_level, step_types=("instant",))
    if value is not None:
        return value
    return scalar_from_datasets(
        datasets,
        names,
        type_of_level=type_of_level,
        step_types=("avg", "average"),
        interval_hours=float(duration_hours),
    )


def _selected_scalar(datasets, selector: GribFieldSelector) -> tuple[float | None, float | None]:
    selected = select_grib_field(datasets, selector)
    if selected is None:
        return None, None
    try:
        value = float(np.asarray(selected.data_array.values).squeeze().flat[0])
    except Exception:
        return None, selected.interval_hours
    if not math.isfinite(value):
        return None, selected.interval_hours
    return value, selected.interval_hours


def _cape_cin_180(datasets) -> tuple[float | None, float | None, str]:
    cape = scalar_from_datasets(
        datasets,
        ("cape",),
        type_of_level=("pressureFromGroundLayer",),
        level=CAPE_LAYER_PA,
        step_types=("instant",),
    )
    cin = scalar_from_datasets(
        datasets,
        ("cin",),
        type_of_level=("pressureFromGroundLayer",),
        level=CAPE_LAYER_PA,
        step_types=("instant",),
    )
    if cape is not None and cin is not None:
        return cape, cin, "180–0 hPa AGL"

    surface_cape = scalar_from_datasets(datasets, ("cape",), type_of_level=("surface",), step_types=("instant",))
    surface_cin = scalar_from_datasets(datasets, ("cin",), type_of_level=("surface",), step_types=("instant",))
    if surface_cape is not None and surface_cin is not None:
        return surface_cape, surface_cin, "surface fallback"
    return None, None, "unavailable"


def _ceiling_agl(datasets) -> tuple[float | None, float | None, float | None]:
    ceiling_msl = scalar_from_datasets(
        datasets,
        ("gh", "h", "hgt"),
        type_of_level=("cloudCeiling",),
        step_types=("instant",),
    )
    surface = scalar_from_datasets(
        datasets,
        ("gh", "h", "hgt"),
        type_of_level=("surface",),
        step_types=("instant",),
    )
    if ceiling_msl is None or surface is None:
        return None, ceiling_msl, surface
    if not math.isfinite(float(ceiling_msl)) or not math.isfinite(float(surface)) or abs(float(ceiling_msl)) > 1e8:
        return None, ceiling_msl, surface
    return max(0.0, float(ceiling_msl) - float(surface)), float(ceiling_msl), float(surface)


def _read_cloudgram_cell(
    run: GfsRun,
    lead_hour: int,
    lat: float,
    lon: float,
    progress_callback: ProgressCallback | None = None,
    duration_hours: int = CLOUDGRAM_DEFAULT_STEP,
) -> tuple[CloudgramCell, float, float, set[str]]:
    path, grid_lat, grid_lon = download_gfs_subset_to_disk(
        run.date,
        run.cycle,
        lead_hour,
        lat,
        lon,
        CLOUDGRAM_VARIABLES,
        CLOUDGRAM_LEVEL_TOKENS,
        product_key="cloudgram_strict_v3",
        progress_callback=progress_callback,
    )
    missing: set[str] = set()
    requested_interval = max(1.0, float(duration_hours))
    with tempfile.TemporaryDirectory() as tmp:
        datasets = open_grib_datasets(path, Path(tmp))
        low = _clip_pct(_instant_or_average(datasets, ("lcc", "lcdc"), ("lowCloudLayer",), requested_interval))
        mid = _clip_pct(_instant_or_average(datasets, ("mcc", "mcdc"), ("middleCloudLayer",), requested_interval))
        high = _clip_pct(_instant_or_average(datasets, ("hcc", "hcdc"), ("highCloudLayer",), requested_interval))
        total = _clip_pct(_instant_or_average(datasets, ("tcc", "tcdc"), ("atmosphere", "entireAtmosphere"), requested_interval))
        conv_cloud = _clip_pct(
            scalar_from_datasets(
                datasets,
                ("tcc", "tcdc"),
                type_of_level=("convectiveCloudLayer",),
                step_types=("instant",),
            )
        )
        ceiling, ceiling_msl, surface_elevation = _ceiling_agl(datasets)

        apcp_value, apcp_interval = _selected_scalar(
            datasets,
            GribFieldSelector(
                names=("tp", "apcp"),
                type_of_level=("surface",),
                step_types=("accum",),
                interval_hours=requested_interval,
            ),
        )
        apcp_total = _mm_from_kgm2(apcp_value)
        precip_interval = float(apcp_interval or requested_interval)
        prate = _mmh_from_prate(
            scalar_from_datasets(datasets, ("prate",), type_of_level=("surface",), step_types=("instant",))
        )
        apcp = _precip_from_total_or_rate(apcp_total, prate, precip_interval)

        acpcp_value, acpcp_interval = _selected_scalar(
            datasets,
            GribFieldSelector(
                names=("acpcp",),
                type_of_level=("surface",),
                step_types=("accum",),
                interval_hours=requested_interval,
            ),
        )
        acpcp = _mm_from_kgm2(acpcp_value)
        conv_interval = float(acpcp_interval or requested_interval)
        cprat = _mmh_from_prate(
            scalar_from_datasets(datasets, ("cprat",), type_of_level=("surface",), step_types=("instant",))
        )
        cape, cin, cape_layer = _cape_cin_180(datasets)
        visibility = _visibility_km(
            scalar_from_datasets(datasets, ("vis", "visibility"), type_of_level=("surface",), step_types=("instant",))
        )
        precip_type = _precip_type_from_flags(
            bool_from_datasets(datasets, ("crain",), type_of_level=("surface",), step_types=("instant",)),
            bool_from_datasets(datasets, ("csnow",), type_of_level=("surface",), step_types=("instant",)),
            bool_from_datasets(datasets, ("cfrzr",), type_of_level=("surface",), step_types=("instant",)),
            bool_from_datasets(datasets, ("cicep",), type_of_level=("surface",), step_types=("instant",)),
        )

    for name, value in {
        "low_cloud": low,
        "mid_cloud": mid,
        "high_cloud": high,
        "total_cloud": total,
        "convective_cloud": conv_cloud,
        "ceiling_agl": ceiling,
        "precip": apcp,
        "visibility": visibility,
        "cape_180_0": cape if cape_layer == "180–0 hPa AGL" else None,
        "cin_180_0": cin if cape_layer == "180–0 hPa AGL" else None,
    }.items():
        if value is None:
            missing.add(name)

    cb = _cb_score(cape, cin, acpcp, conv_cloud, cprat, conv_interval)
    phenomena = _phenomena(apcp, precip_type, cb, visibility)
    hazard, hazard_text = _hazard_score(cb, apcp, ceiling, visibility, phenomena, precip_interval)
    valid_time = run.run_datetime_utc + timedelta(hours=lead_hour)

    return (
        CloudgramCell(
            lead_hour=lead_hour,
            valid_time_utc=valid_time,
            high_cloud_pct=high,
            mid_cloud_pct=mid,
            low_cloud_pct=low,
            total_cloud_pct=total,
            ceiling_m=ceiling,
            precip_mm=apcp,
            precip_rate_mmh=prate,
            conv_precip_mm=acpcp,
            precip_type=precip_type,
            cape_jkg=cape,
            cin_jkg=cin,
            cb_score=cb,
            visibility_km=visibility,
            phenomena=phenomena,
            hazard_score=hazard,
            hazard_text=hazard_text,
            ceiling_msl_gpm=ceiling_msl,
            surface_elevation_gpm=surface_elevation,
            convective_cloud_pct=conv_cloud,
            conv_precip_rate_mmh=cprat,
            cape_layer=cape_layer,
            precip_interval_hours=precip_interval,
            conv_precip_interval_hours=conv_interval,
        ),
        grid_lat,
        grid_lon,
        missing,
    )


def build_cloudgram_data(
    run: GfsRun,
    lat: float,
    lon: float,
    lead_from: int = 0,
    lead_to: int = CLOUDGRAM_DEFAULT_TO,
    step: int = CLOUDGRAM_DEFAULT_STEP,
    progress_callback: ProgressCallback | None = None,
) -> CloudgramData:
    leads = cloudgram_leads(lead_from, lead_to, step)
    cells: list[CloudgramCell] = []
    missing: set[str] = set()
    grid_lat = lat
    grid_lon = lon
    total = len(leads)
    for index, lead in enumerate(leads, start=1):
        if progress_callback:
            progress_callback({"stage": "cloudgram_step", "message": "готовлю cloudgram", "index": index, "total": total, "lead_hour": lead})
        cell, grid_lat, grid_lon, cell_missing = _read_cloudgram_cell(
            run,
            lead,
            lat,
            lon,
            progress_callback=progress_callback,
            duration_hours=step,
        )
        cells.append(cell)
        missing.update(cell_missing)
    return CloudgramData(run, lat, lon, grid_lat, grid_lon, leads, cells, tuple(sorted(missing)))
