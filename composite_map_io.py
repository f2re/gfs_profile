from __future__ import annotations

import hashlib
import math
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import requests
from requests import RequestException

from gfs_core import (
    CACHE_DIR,
    NOMADS_BASE,
    REQUEST_TIMEOUT,
    GfsProfileError,
    ProgressCallback,
    clean_old_cache,
    forecast_file_exists,
    run_dir,
    run_file_name,
    validate_lead,
)

MAP_RADIUS_KM = 100.0
MAP_RING_STEP_KM = 25.0
MAP_MAX_ANIMATION_FRAMES = 18
MAP_MAX_PNG_SERIES_FRAMES = 18
MAP_BASEMAP_BASIC = "basic"
MAP_BASEMAP_WATER = "water"
MAP_BASEMAP_PLACES = "places"
MAP_BASEMAP_ROADS = "roads"
MAP_BASEMAP_DEFAULT = MAP_BASEMAP_PLACES
MAP_BASEMAPS = (MAP_BASEMAP_BASIC, MAP_BASEMAP_WATER, MAP_BASEMAP_PLACES, MAP_BASEMAP_ROADS)
MAP_VARIABLES = (
    "TCDC",
    "APCP",
    "PRATE",
    "ACPCP",
    "CPRAT",
    "CAPE",
    "CIN",
    "VIS",
    "CRAIN",
    "CSNOW",
    "CFRZR",
    "CICEP",
    "UGRD",
    "VGRD",
)
MAP_LEVEL_TOKENS = (
    "lev_entire_atmosphere",
    "lev_surface",
    "lev_500_mb",
    "lev_180-0_mb_above_ground",
    "lev_convective_cloud_layer",
)


def area_box_from_radius(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    if radius_km <= 0 or radius_km > 300:
        raise GfsProfileError("Радиус карты должен быть в диапазоне 1..300 км")
    lon = float(_lon180(lon))
    dlat = radius_km / 110.574
    cos_lat = max(0.08, abs(math.cos(math.radians(lat))))
    dlon = radius_km / (111.320 * cos_lat)
    south = round(max(-90.0, lat - dlat), 3)
    north = round(min(90.0, lat + dlat), 3)
    west = round(lon - dlon, 3)
    east = round(lon + dlon, 3)
    return south, north, west, east


def _box_token(box: tuple[float, float, float, float]) -> str:
    south, north, west, east = box
    return f"s{south:.3f}_n{north:.3f}_w{west:.3f}_e{east:.3f}".replace("-", "m")


def _emit(progress_callback: ProgressCallback | None, **payload) -> None:
    if progress_callback:
        progress_callback(payload)


def _area_subset_url(date: str, cycle: str, lead_hour: int, box: tuple[float, float, float, float]) -> str:
    south, north, west, east = box
    if west < -180.0 or east > 180.0:
        leftlon = west % 360.0
        rightlon = east % 360.0
        if rightlon <= leftlon:
            rightlon += 360.0
    else:
        leftlon = west
        rightlon = east
    query = {
        "file": run_file_name(cycle, lead_hour),
        "subregion": "",
        "leftlon": f"{leftlon:.3f}",
        "rightlon": f"{rightlon:.3f}",
        "toplat": f"{north:.3f}",
        "bottomlat": f"{south:.3f}",
        "dir": run_dir(date, cycle),
    }
    for variable in MAP_VARIABLES:
        query[f"var_{variable}"] = "on"
    for level_token in MAP_LEVEL_TOKENS:
        query[level_token] = "on"
    return f"{NOMADS_BASE}/cgi-bin/filter_gfs_0p25_1hr.pl?{urlencode(query)}"


def _validate_grib_magic(path: Path) -> None:
    try:
        with path.open("rb") as file_obj:
            magic = file_obj.read(4)
    except OSError as exc:
        raise GfsProfileError(f"Не удалось прочитать GRIB2 карты: {exc}") from exc
    if magic != b"GRIB":
        path.unlink(missing_ok=True)
        raise GfsProfileError("NOMADS вернул ответ без сигнатуры GRIB")


def download_area_subset(
    date: str,
    cycle: str,
    lead_hour: int,
    lat: float,
    lon: float,
    radius_km: float,
    progress_callback: ProgressCallback | None = None,
) -> tuple[Path, tuple[float, float, float, float]]:
    validate_lead(lead_hour)
    clean_old_cache()
    box = area_box_from_radius(lat, lon, radius_km)
    digest_payload = ",".join(MAP_VARIABLES) + "|" + ",".join(MAP_LEVEL_TOKENS)
    digest = hashlib.sha1(digest_payload.encode("utf-8")).hexdigest()[:12]
    key = f"map_{date}_{cycle}_f{lead_hour:03d}_{_box_token(box)}_{digest}"
    out_path = CACHE_DIR / f"{key}.grib2"
    if out_path.exists():
        _validate_grib_magic(out_path)
        _emit(progress_callback, stage="map_cache", message="GRIB2 карты найден в кэше", radius_km=radius_km)
        return out_path, box
    if not forecast_file_exists(date, cycle, lead_hour):
        raise GfsProfileError(f"Файл GFS для {date} {cycle}Z +{lead_hour} ч ещё не опубликован")

    url = _area_subset_url(date, cycle, lead_hour, box)
    lock_path = CACHE_DIR / f"{key}.lock"
    lock_fd: int | None = None
    wait_started = time.monotonic()
    while lock_fd is None:
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, str(os.getpid()).encode("ascii", errors="ignore"))
        except FileExistsError:
            if out_path.exists():
                _validate_grib_magic(out_path)
                _emit(progress_callback, stage="map_cache", message="GRIB2 карты найден в кэше", radius_km=radius_km)
                return out_path, box
            if time.monotonic() - wait_started > 600:
                lock_path.unlink(missing_ok=True)
                wait_started = time.monotonic()
                continue
            _emit(progress_callback, stage="map_cache", message="Жду параллельную загрузку spatial GRIB2", radius_km=radius_km)
            time.sleep(1.0)

    part_path = CACHE_DIR / f"{key}.{os.getpid()}.{int(time.time() * 1000)}.part"
    _emit(
        progress_callback,
        stage="map_download_start",
        message="Скачиваю пространственный GRIB2",
        downloaded=0,
        total=None,
        radius_km=radius_km,
    )
    try:
        if out_path.exists():
            _validate_grib_magic(out_path)
            _emit(progress_callback, stage="map_cache", message="GRIB2 карты найден в кэше", radius_km=radius_km)
            return out_path, box
        with requests.get(url, timeout=REQUEST_TIMEOUT, stream=True) as response:
            if response.status_code != 200:
                raise GfsProfileError(f"Ошибка загрузки GFS-карты: HTTP {response.status_code}")
            if "text/html" in response.headers.get("content-type", "").lower():
                raise GfsProfileError("NOMADS вернул HTML вместо GRIB2 карты")
            total = int(response.headers.get("content-length") or 0) or None
            downloaded = 0
            last_emit = 0.0
            with part_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    file_obj.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_emit >= 1.0:
                        last_emit = now
                        _emit(
                            progress_callback,
                            stage="map_download",
                            message="Скачиваю пространственный GRIB2",
                            downloaded=downloaded,
                            total=total,
                            radius_km=radius_km,
                        )
            _emit(
                progress_callback,
                stage="map_download_done",
                message="GRIB2 карты загружен",
                downloaded=downloaded,
                total=total,
                radius_km=radius_km,
            )
        if not part_path.exists() or part_path.stat().st_size < 256:
            part_path.unlink(missing_ok=True)
            raise GfsProfileError("Получен слишком маленький ответ от GFS Filter для карты")
        _validate_grib_magic(part_path)
        part_path.replace(out_path)
        return out_path, box
    except RequestException as exc:
        part_path.unlink(missing_ok=True)
        raise GfsProfileError(f"Ошибка подключения к NOMADS: {exc}") from exc
    except Exception:
        part_path.unlink(missing_ok=True)
        raise
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        lock_path.unlink(missing_ok=True)


def _lon180(values):
    arr = np.asarray(values, dtype=float)
    return ((arr + 180.0) % 360.0) - 180.0


def _lon_delta(lon_values, center_lon: float):
    return ((np.asarray(lon_values, dtype=float) - float(_lon180(center_lon)) + 180.0) % 360.0) - 180.0


def _coords_from_dataarray(da):
    lat_name = "latitude" if "latitude" in da.coords else "lat" if "lat" in da.coords else None
    lon_name = "longitude" if "longitude" in da.coords else "lon" if "lon" in da.coords else None
    if not lat_name or not lon_name:
        raise ValueError("нет координат latitude/longitude")
    lat = np.asarray(da[lat_name].values, dtype=float)
    lon = _lon180(da[lon_name].values)
    if lat.ndim == 1 and lon.ndim == 1:
        lon2d, lat2d = np.meshgrid(lon, lat)
    else:
        lat2d = lat
        lon2d = lon
    return lat2d, lon2d


def _xy_km(lat2d, lon2d, center_lat: float, center_lon: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = _lon_delta(lon2d, center_lon) * 111.320 * math.cos(math.radians(center_lat))
    y = (lat2d - center_lat) * 110.574
    dist = np.sqrt(x * x + y * y)
    return x, y, dist


def _xy_point(lat: float, lon: float, center_lat: float, center_lon: float) -> tuple[float, float]:
    x = float(_lon_delta(lon, center_lon)) * 111.320 * math.cos(math.radians(center_lat))
    y = (float(lat) - center_lat) * 110.574
    return x, y


def _validate_basemap(basemap: str) -> str:
    value = str(basemap or MAP_BASEMAP_DEFAULT).lower()
    if value not in MAP_BASEMAPS:
        raise GfsProfileError(f"Неизвестная подложка карты: {basemap}")
    return value
