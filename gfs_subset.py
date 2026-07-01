from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from requests import RequestException

from gfs_core import (
    CACHE_DIR,
    NOMADS_BASE,
    REQUEST_TIMEOUT,
    GfsProfileError,
    ProgressCallback,
    _acquire_download_lock,
    _emit,
    _release_download_lock,
    _validate_grib_file,
    clean_old_cache,
    forecast_file_exists,
    run_dir,
    run_file_name,
    snap_to_gfs_grid,
    validate_lead,
)


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")


def subset_cache_key(
    date: str,
    cycle: str,
    lead_hour: int,
    lat: float,
    lon: float,
    variables: tuple[str, ...],
    level_tokens: tuple[str, ...],
    product_key: str,
) -> str:
    payload = ",".join(sorted(variables)) + "|" + ",".join(sorted(level_tokens))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"{product_key}_{date}_{cycle}_f{lead_hour:03d}_{lat:.3f}_{lon:.3f}_{digest}".replace("-", "m")


def gfs_subset_url(
    date: str,
    cycle: str,
    lead_hour: int,
    lat: float,
    lon: float,
    variables: tuple[str, ...],
    level_tokens: tuple[str, ...],
) -> str:
    lon_360 = lon % 360
    top_lat = min(90.0, lat + 0.001)
    bottom_lat = max(-90.0, lat)
    query: dict[str, str] = {
        "file": run_file_name(cycle, lead_hour),
        "subregion": "",
        "leftlon": f"{lon_360:.3f}",
        "rightlon": f"{lon_360 + 0.001:.3f}",
        "toplat": f"{top_lat:.3f}",
        "bottomlat": f"{bottom_lat:.3f}",
        "dir": run_dir(date, cycle),
    }
    for variable in variables:
        query[f"var_{variable.upper()}"] = "on"
    for level_token in level_tokens:
        query[level_token] = "on"
    return f"{NOMADS_BASE}/cgi-bin/filter_gfs_0p25_1hr.pl?{urlencode(query)}"


def download_gfs_subset_to_disk(
    date: str,
    cycle: str,
    lead_hour: int,
    lat: float,
    lon: float,
    variables: tuple[str, ...],
    level_tokens: tuple[str, ...],
    product_key: str = "subset",
    progress_callback: ProgressCallback | None = None,
) -> tuple[Path, float, float]:
    validate_lead(lead_hour)
    if not variables:
        raise GfsProfileError("Не задан список GFS-переменных для subset")
    if not level_tokens:
        raise GfsProfileError("Не задан список GFS-уровней для subset")

    grid_lat, grid_lon = snap_to_gfs_grid(lat, lon)
    clean_old_cache()
    key = subset_cache_key(date, cycle, lead_hour, grid_lat, grid_lon, variables, level_tokens, product_key)
    out_path = CACHE_DIR / f"{key}.grib2"
    lock_entry = _acquire_download_lock(key)
    try:
        with lock_entry.lock:
            if out_path.exists():
                try:
                    _validate_grib_file(out_path)
                    _emit(progress_callback, stage="cache", message="GRIB2 subset найден в кэше", file=str(out_path), bytes=out_path.stat().st_size)
                    return out_path, grid_lat, grid_lon
                except GfsProfileError:
                    out_path.unlink(missing_ok=True)

            if not forecast_file_exists(date, cycle, lead_hour):
                raise GfsProfileError(f"Файл GFS для {date} {cycle}Z +{lead_hour} ч ещё не опубликован")

            url = gfs_subset_url(date, cycle, lead_hour, grid_lat, grid_lon, variables, level_tokens)
            part_path = CACHE_DIR / f"{key}.part"
            part_path.unlink(missing_ok=True)
            _emit(
                progress_callback,
                stage="download_start",
                message="Начинаю загрузку GFS subset",
                url=url,
                variables=list(variables),
                levels=list(level_tokens),
                downloaded=0,
                total=None,
            )
            try:
                with requests.get(url, timeout=REQUEST_TIMEOUT, stream=True) as response:
                    if response.status_code != 200:
                        raise GfsProfileError(f"Ошибка загрузки GFS subset: HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" in content_type:
                        raise GfsProfileError("NOMADS вернул HTML вместо GRIB2 subset")

                    total = int(response.headers.get("content-length") or 0) or None
                    downloaded = 0
                    last_emit = 0.0
                    with part_path.open("wb") as file_obj:
                        for chunk in response.iter_content(chunk_size=65536):
                            if chunk:
                                file_obj.write(chunk)
                                downloaded += len(chunk)
                                now = time.monotonic()
                                if now - last_emit >= 1.0:
                                    last_emit = now
                                    _emit(progress_callback, stage="download", message="Загружаю GFS subset", downloaded=downloaded, total=total)
                    _emit(progress_callback, stage="download_done", message="GFS subset загружен", downloaded=downloaded, total=total)
            except RequestException as exc:
                part_path.unlink(missing_ok=True)
                raise GfsProfileError(f"Ошибка подключения к NOMADS: {exc}") from exc
            except Exception:
                part_path.unlink(missing_ok=True)
                raise

            if not part_path.exists() or part_path.stat().st_size < 128:
                part_path.unlink(missing_ok=True)
                raise GfsProfileError("Получен слишком маленький ответ от GFS Filter для subset")
            _validate_grib_file(part_path)
            part_path.replace(out_path)
            return out_path, grid_lat, grid_lon
    finally:
        _release_download_lock(key, lock_entry)


def open_grib_datasets(path: Path, index_dir: Path) -> list[Any]:
    try:
        import cfgrib
    except Exception as exc:
        raise GfsProfileError("Не установлен cfgrib. Выполните pip install -r requirements.txt") from exc

    try:
        return list(
            cfgrib.open_datasets(
                str(path),
                backend_kwargs={"indexpath": str(index_dir / (_safe_token(path.stem) + ".idx")), "errors": "ignore"},
            )
        )
    except Exception as exc:
        raise GfsProfileError(f"Ошибка чтения GFS subset через cfgrib: {exc}") from exc


def scalar_from_datasets(datasets: list[Any], names: tuple[str, ...], default: float | None = None) -> float | None:
    for ds in datasets:
        for name in names:
            if name in ds:
                try:
                    value = ds[name].values
                    import numpy as np

                    return float(np.asarray(value).squeeze().flat[0])
                except Exception:
                    continue
    return default


def bool_from_datasets(datasets: list[Any], names: tuple[str, ...]) -> bool:
    value = scalar_from_datasets(datasets, names, default=0.0)
    return bool(value is not None and value >= 0.5)
