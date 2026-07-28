from __future__ import annotations

from pathlib import Path

import pandas as pd

from gfs_core import (
    CACHE_DIR,
    GfsProfileError,
    GfsRun,
    ProfileResult,
    ProgressCallback,
    _acquire_download_lock,
    _download_profile_grib_to_disk_unlocked,
    _emit,
    _release_download_lock,
    cache_key,
    clean_old_cache,
    extract_profile_from_grib_file,
    forecast_file_exists,
    invalidate_grib_cache_file,
    snap_to_gfs_grid,
    validate_lead,
)


def download_profile_grib_to_disk_for_levels(
    date: str,
    cycle: str,
    lead_hour: int,
    lat: float,
    lon: float,
    levels_hpa: tuple[int, ...] | None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """Download a point-profile GRIB2 subset with an explicit pressure-level set.

    This wrapper keeps the existing gfs_core public API stable while allowing
    product modules such as aero diagrams and windgrams to request narrower
    NOMADS subsets and independent cache keys.
    """

    validate_lead(lead_hour)
    clean_old_cache()
    key = cache_key(date, cycle, lead_hour, lat, lon, levels_hpa)
    out_path = CACHE_DIR / f"{key}.grib2"

    lock_entry = _acquire_download_lock(key)
    try:
        with lock_entry.lock:
            return _download_profile_grib_to_disk_unlocked(
                date,
                cycle,
                lead_hour,
                lat,
                lon,
                levels_hpa,
                key,
                out_path,
                progress_callback=progress_callback,
            )
    finally:
        _release_download_lock(key, lock_entry)


def build_profile_for_levels(
    run: GfsRun,
    lead_hour: int,
    lat: float,
    lon: float,
    levels_hpa: tuple[int, ...] | None,
    progress_callback: ProgressCallback | None = None,
) -> ProfileResult:
    """Build a profile using an explicit pressure-level set.

    levels_hpa=None means all isobaric levels, exactly as the existing NOMADS
    helper represents all levels. A tuple requests only selected levels.
    """

    validate_lead(lead_hour)
    _emit(progress_callback, stage="check", message="Проверяю публикацию forecast-файла", run=f"{run.date} {run.cycle}Z", lead_hour=lead_hour)
    if not forecast_file_exists(run.date, run.cycle, lead_hour):
        raise GfsProfileError(f"Для указанной даты/цикла/срока данные GFS недоступны: {run.date} {run.cycle}Z +{lead_hour} ч")

    grid_lat, grid_lon = snap_to_gfs_grid(lat, lon)
    _emit(progress_callback, stage="grid", message="Точка привязана к узлу GFS", grid_lat=grid_lat, grid_lon=grid_lon)
    grib_path = download_profile_grib_to_disk_for_levels(
        run.date,
        run.cycle,
        lead_hour,
        grid_lat,
        grid_lon,
        levels_hpa,
        progress_callback=progress_callback,
    )
    try:
        df = extract_profile_from_grib_file(grib_path, progress_callback=progress_callback)
    except GfsProfileError as exc:
        if "Ошибка чтения GRIB2" in str(exc):
            invalidate_grib_cache_file(grib_path)
        raise

    if not isinstance(df, pd.DataFrame):
        raise GfsProfileError("Внутренняя ошибка: профиль не является DataFrame")

    _emit(progress_callback, stage="done", message="Профиль готов", rows=len(df))
    return ProfileResult(
        run=run,
        lead_hour=lead_hour,
        requested_lat=lat,
        requested_lon=lon,
        grid_lat=grid_lat,
        grid_lon=grid_lon,
        grib_path=grib_path,
        dataframe=df,
    )
