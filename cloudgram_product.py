from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gfs_core import GfsProfileError, GfsRun, ProgressCallback, canonical_leads
from gfs_subset import bool_from_datasets, download_gfs_subset_to_disk, open_grib_datasets, scalar_from_datasets

CLOUDGRAM_DEFAULT_TO = 72
CLOUDGRAM_DEFAULT_STEP = 3
CLOUDGRAM_MAX_TO = 120

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
    "lev_90-0_mb_above_ground",
    "lev_255-0_mb_above_ground",
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
        raise GfsProfileError(f"cloudgram ограничен +{CLOUDGRAM_MAX_TO} ч; используйте меньший to")
    allowed = set(canonical_leads())
    leads = [lead for lead in range(lead_from, lead_to + 1, step) if lead in allowed]
    if not leads:
        raise GfsProfileError("В диапазоне cloudgram нет допустимых сроков GFS")
    return leads


def _clip_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))


def _mm_from_kgm2(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, float(value))


def _mmh_from_prate(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, float(value) * 3600.0)


def _precip_type_from_flags(rain: bool, snow: bool, freezing: bool, ice_pellets: bool) -> str:
    parts: list[str] = []
    if rain:
        parts.append("R")
    if snow:
        parts.append("S")
    if freezing:
        parts.append("FZ")
    if ice_pellets:
        parts.append("IP")
    return "/".join(parts) if parts else "—"


def _cb_score(cape: float | None, cin: float | None, conv_precip_mm: float | None, conv_cloud_pct: float | None, precip_rate_mmh: float | None) -> int:
    score = 0
    if cape is not None:
        if cape >= 1000:
            score += 2
        elif cape >= 250:
            score += 1
    if cin is not None and cin > -150:
        score += 1
    if conv_precip_mm is not None and conv_precip_mm >= 0.2:
        score += 1
    if conv_cloud_pct is not None and conv_cloud_pct >= 30:
        score += 1
    if precip_rate_mmh is not None and precip_rate_mmh >= 3.0:
        score += 1
    return max(0, min(3, score))


def _read_cloudgram_cell(run: GfsRun, lead_hour: int, lat: float, lon: float, progress_callback: ProgressCallback | None = None) -> tuple[CloudgramCell, float, float, set[str]]:
    path, grid_lat, grid_lon = download_gfs_subset_to_disk(
        run.date,
        run.cycle,
        lead_hour,
        lat,
        lon,
        CLOUDGRAM_VARIABLES,
        CLOUDGRAM_LEVEL_TOKENS,
        product_key="cloudgram",
        progress_callback=progress_callback,
    )
    missing: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        datasets = open_grib_datasets(path, Path(tmp))

    low = _clip_pct(scalar_from_datasets(datasets, ("lcc", "lcdc")))
    mid = _clip_pct(scalar_from_datasets(datasets, ("mcc", "mcdc")))
    high = _clip_pct(scalar_from_datasets(datasets, ("hcc", "hcdc")))
    total = _clip_pct(scalar_from_datasets(datasets, ("tcc", "tcdc")))
    ceiling = scalar_from_datasets(datasets, ("gh", "h", "hgt"))
    apcp = _mm_from_kgm2(scalar_from_datasets(datasets, ("tp", "apcp")))
    prate = _mmh_from_prate(scalar_from_datasets(datasets, ("prate",)))
    acpcp = _mm_from_kgm2(scalar_from_datasets(datasets, ("acpcp",)))
    cprat = _mmh_from_prate(scalar_from_datasets(datasets, ("cprat",)))
    cape = scalar_from_datasets(datasets, ("cape",))
    cin = scalar_from_datasets(datasets, ("cin",))
    conv_cloud = _clip_pct(scalar_from_datasets(datasets, ("tcc", "tcdc"), default=None))

    for name, value in {
        "low_cloud": low,
        "mid_cloud": mid,
        "high_cloud": high,
        "total_cloud": total,
        "ceiling": ceiling,
        "precip": apcp,
    }.items():
        if value is None:
            missing.add(name)

    precip_type = _precip_type_from_flags(
        bool_from_datasets(datasets, ("crain",)),
        bool_from_datasets(datasets, ("csnow",)),
        bool_from_datasets(datasets, ("cfrzr",)),
        bool_from_datasets(datasets, ("cicep",)),
    )
    cb = _cb_score(cape, cin, acpcp, conv_cloud, cprat or prate)
    valid_time = run.run_datetime_utc.replace() + __import__("datetime").timedelta(hours=lead_hour)

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
        cell, grid_lat, grid_lon, cell_missing = _read_cloudgram_cell(run, lead, lat, lon, progress_callback=progress_callback)
        cells.append(cell)
        missing.update(cell_missing)
    return CloudgramData(run, lat, lon, grid_lat, grid_lon, leads, cells, tuple(sorted(missing)))
