from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.colors import BoundaryNorm, ListedColormap
from PIL import Image
from requests import RequestException

from basemap_cache import local_basemap_overlay
from geocode import GeoPoint
from gfs_core import CACHE_DIR, NOMADS_BASE, REQUEST_TIMEOUT, GfsProfileError, GfsRun, ProgressCallback, clean_old_cache, forecast_file_exists, run_dir, run_file_name, validate_lead
from weather_diagnostics import DASH, precipitation_code, thunder_score, visibility_km, weather_code

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
    "TCDC", "APCP", "PRATE", "ACPCP", "CPRAT", "CAPE", "CIN", "VIS",
    "CRAIN", "CSNOW", "CFRZR", "CICEP", "UGRD", "VGRD",
)
MAP_LEVEL_TOKENS = ("lev_entire_atmosphere", "lev_surface", "lev_500_mb", "lev_180-0_mb_above_ground", "lev_convective_cloud_layer")
WEATHER_CODE_ICONS = {"RA": "☔", "SN": "❄", "FZRA": "❄", "FG": "≋", "TSRA": "⚡"}


def weather_code_icon(code: str) -> str:
    return WEATHER_CODE_ICONS.get(code, "")


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


def download_area_subset(date: str, cycle: str, lead_hour: int, lat: float, lon: float, radius_km: float, progress_callback: ProgressCallback | None = None) -> tuple[Path, tuple[float, float, float, float]]:
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
    _emit(progress_callback, stage="map_download_start", message="Скачиваю пространственный GRIB2", downloaded=0, total=None, radius_km=radius_km)
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
                        _emit(progress_callback, stage="map_download", message="Скачиваю пространственный GRIB2", downloaded=downloaded, total=total, radius_km=radius_km)
            _emit(progress_callback, stage="map_download_done", message="GRIB2 карты загружен", downloaded=downloaded, total=total, radius_km=radius_km)
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


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:120]


def _open_datasets(path: Path, index_dir: Path):
    try:
        import cfgrib
    except Exception as exc:
        raise GfsProfileError("Не установлен cfgrib. Выполните pip install -r requirements.txt") from exc
    try:
        return list(cfgrib.open_datasets(str(path), backend_kwargs={"indexpath": str(index_dir / (_safe_token(path.stem) + ".idx")), "errors": "ignore"}))
    except Exception as exc:
        raise GfsProfileError(f"Ошибка чтения GFS-карты через cfgrib: {exc}") from exc


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


def _dataset_type_of_level(ds) -> str:
    value = ds.attrs.get("GRIB_typeOfLevel", "")
    return str(value or "")


def _dataarray_type_of_level(da) -> str:
    value = da.attrs.get("GRIB_typeOfLevel", "")
    return str(value or "")


def summarize_cfgrib_datasets(datasets) -> list[dict]:
    summary: list[dict] = []
    for index, ds in enumerate(datasets):
        item = {
            "index": index,
            "variables": list(getattr(ds, "data_vars", {}).keys()),
            "coords": list(getattr(ds, "coords", {}).keys()),
            "attrs": {key: ds.attrs.get(key) for key in ("GRIB_shortName", "GRIB_typeOfLevel", "GRIB_level") if key in ds.attrs},
            "data_vars": {},
        }
        for name, da in getattr(ds, "data_vars", {}).items():
            item["data_vars"][name] = {
                "coords": list(getattr(da, "coords", {}).keys()),
                "attrs": {key: da.attrs.get(key) for key in ("GRIB_shortName", "GRIB_typeOfLevel", "GRIB_level") if key in da.attrs},
            }
        summary.append(item)
    return summary


def _dataset_var(ds, names: tuple[str, ...]):
    data_vars = getattr(ds, "data_vars", {})
    lower_names = {name.lower() for name in names}
    for name in names:
        if name in ds:
            return ds[name]
    for actual in data_vars.keys():
        if str(actual).lower() in lower_names:
            return ds[actual]
        short_name = str(data_vars[actual].attrs.get("GRIB_shortName", "")).lower()
        if short_name and short_name in lower_names:
            return ds[actual]
    return None


def _select_level(da, level_hpa: int | None):
    if level_hpa is None:
        return da
    targets = {
        "isobaricInhPa": float(level_hpa),
        "isobaricInPa": float(level_hpa) * 100.0,
        "level": float(level_hpa),
    }
    for coord_name, target in targets.items():
        if coord_name not in da.coords:
            continue
        try:
            coord_values = np.asarray(da[coord_name].values, dtype=float)
            tolerance = 100.0 if coord_name == "isobaricInPa" else 1.0
            if coord_values.ndim == 0:
                return da if abs(float(coord_values) - target) <= tolerance else None
            nearest = float(coord_values.flat[np.argmin(np.abs(coord_values - target))])
            if abs(nearest - target) > tolerance:
                return None
            return da.sel({coord_name: target}, method="nearest")
        except Exception:
            continue
    for key in ("GRIB_level", "level"):
        if key in da.attrs:
            try:
                return da if int(float(da.attrs[key])) == int(level_hpa) else None
            except Exception:
                pass
    return None


def _field(datasets, names: tuple[str, ...], level_hpa: int | None = None, type_of_level: tuple[str, ...] | None = None):
    for ds in datasets:
        ds_type = _dataset_type_of_level(ds)
        try:
            da = _dataset_var(ds, names)
            if da is None:
                continue
            da_type = _dataarray_type_of_level(da) or ds_type
            if type_of_level is not None and da_type not in type_of_level:
                continue
            da = _select_level(da, level_hpa)
            if da is None:
                continue
            values = np.asarray(da.values).squeeze().astype(float)
            while values.ndim > 2:
                values = values[0]
            lat2d, lon2d = _coords_from_dataarray(da)
            if values.shape != lat2d.shape and values.T.shape == lat2d.shape:
                values = values.T
            return values, lat2d, lon2d
        except Exception:
            continue
    return None


def _bool_field(datasets, names: tuple[str, ...], type_of_level: tuple[str, ...] | None = None):
    item = _field(datasets, names, type_of_level=type_of_level)
    if item is None:
        return None
    values, lat2d, lon2d = item
    return values >= 0.5, lat2d, lon2d


def _mm_from_total(values):
    if values is None:
        return None
    return np.maximum(0.0, values)


def _mmh_from_rate(values):
    if values is None:
        return None
    return np.maximum(0.0, values * 3600.0)


def _clip_pct(values):
    if values is None:
        return None
    return np.clip(values, 0.0, 100.0)


def _xy_km(lat2d, lon2d, center_lat: float, center_lon: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = _lon_delta(lon2d, center_lon) * 111.320 * math.cos(math.radians(center_lat))
    y = (lat2d - center_lat) * 110.574
    dist = np.sqrt(x * x + y * y)
    return x, y, dist


def _storm_grid(cape, cin, conv_precip, conv_cloud, precip_rate):
    ref = None
    for candidate in (cape, cin, conv_precip, conv_cloud, precip_rate):
        if candidate is not None:
            ref = candidate
            break
    if ref is None:
        return None
    missing = np.full_like(ref, np.nan, dtype=float)

    def score(ca, ci, cp, cc, pr):
        def optional(value):
            return None if np.isnan(value) else float(value)

        return float(thunder_score(optional(ca), optional(ci), optional(cp), optional(cc), optional(pr)))

    return np.vectorize(score, otypes=[float])(
        cape if cape is not None else missing,
        cin if cin is not None else missing,
        conv_precip if conv_precip is not None else missing,
        conv_cloud if conv_cloud is not None else missing,
        precip_rate if precip_rate is not None else missing,
    )


def _validate_basemap(basemap: str) -> str:
    value = str(basemap or MAP_BASEMAP_DEFAULT).lower()
    if value not in MAP_BASEMAPS:
        raise GfsProfileError(f"Неизвестная подложка карты: {basemap}")
    return value


def build_composite_map(run: GfsRun, lead_hour: int, point: GeoPoint, radius_km: float = MAP_RADIUS_KM, basemap: str = MAP_BASEMAP_DEFAULT, progress_callback: ProgressCallback | None = None) -> dict:
    basemap = _validate_basemap(basemap)
    _emit(progress_callback, stage="map_cycle", message="Выбираю опубликованный цикл GFS")
    path, box = download_area_subset(run.date, run.cycle, lead_hour, point.lat, point.lon, radius_km, progress_callback)
    _emit(progress_callback, stage="map_parse", message="Читаю GRIB2 через cfgrib/eccodes")
    missing: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        datasets = _open_datasets(path, Path(tmp))
        base_item = (
            _field(datasets, ("tcc", "tcdc"), type_of_level=("atmosphere", "entireAtmosphere"))
            or _field(datasets, ("tp", "apcp"), type_of_level=("surface",))
            or _field(datasets, ("prate",), type_of_level=("surface",))
            or _field(datasets, ("u", "ugrd", "UGRD"), level_hpa=500)
        )
        if base_item is None:
            raise GfsProfileError("В GRIB2 карты не найдены пригодные 2D-поля")
        _, lat2d, lon2d = base_item
        x, y, dist = _xy_km(lat2d, lon2d, point.lat, point.lon)
        radius_mask = dist <= radius_km

        apcp_item = _field(datasets, ("tp", "apcp"), type_of_level=("surface",))
        prate_item = _field(datasets, ("prate",), type_of_level=("surface",))
        cloud_item = _field(datasets, ("tcc", "tcdc"), type_of_level=("atmosphere", "entireAtmosphere"))
        acpcp_item = _field(datasets, ("acpcp",), type_of_level=("surface",))
        cprat_item = _field(datasets, ("cprat",), type_of_level=("surface", "convectiveCloudLayer"))
        cape_item = _field(datasets, ("cape",), type_of_level=("surface",))
        cin_item = _field(datasets, ("cin",), type_of_level=("surface",))
        vis_item = _field(datasets, ("vis", "visibility"), type_of_level=("surface",))
        u_item = _field(datasets, ("u", "ugrd", "UGRD"), level_hpa=500)
        v_item = _field(datasets, ("v", "vgrd", "VGRD"), level_hpa=500)

        precip = _mm_from_total(apcp_item[0]) if apcp_item is not None else None
        prate = _mmh_from_rate(prate_item[0]) if prate_item is not None else None
        if precip is None and prate is not None:
            precip = prate
        cloud = _clip_pct(cloud_item[0]) if cloud_item is not None else None
        conv_precip = _mm_from_total(acpcp_item[0]) if acpcp_item is not None else None
        conv_rate = _mmh_from_rate(cprat_item[0]) if cprat_item is not None else None
        cape = cape_item[0] if cape_item is not None else None
        cin = cin_item[0] if cin_item is not None else None
        vis = np.vectorize(visibility_km, otypes=[float])(vis_item[0]) if vis_item is not None else None
        u500 = u_item[0] if u_item is not None else None
        v500 = v_item[0] if v_item is not None else None

        storm = _storm_grid(cape, cin, conv_precip, cloud, conv_rate if conv_rate is not None else prate)
        rain = _bool_field(datasets, ("crain",), type_of_level=("surface",))
        snow = _bool_field(datasets, ("csnow",), type_of_level=("surface",))
        cold = _bool_field(datasets, ("cfrzr",), type_of_level=("surface",))
        ice = _bool_field(datasets, ("cicep",), type_of_level=("surface",))
        rain_grid = rain[0] if rain is not None else np.zeros_like(lat2d, dtype=bool)
        snow_grid = snow[0] if snow is not None else np.zeros_like(lat2d, dtype=bool)
        cold_grid = cold[0] if cold is not None else np.zeros_like(lat2d, dtype=bool)
        ice_grid = ice[0] if ice is not None else np.zeros_like(lat2d, dtype=bool)

    for name, value in {"осадки": precip, "облачность": cloud, "гроза": storm, "ветер_500": u500 if v500 is not None else None, "видимость": vis}.items():
        if value is None:
            missing.add(name)
    valid_time = run.run_datetime_utc + timedelta(hours=lead_hour)
    _emit(progress_callback, stage="map_done", message="Поля карты подготовлены")
    return {
        "run": run,
        "lead_hour": lead_hour,
        "valid_time": valid_time,
        "point": point,
        "radius_km": radius_km,
        "box": box,
        "basemap": basemap,
        "x": x,
        "y": y,
        "dist": dist,
        "mask": radius_mask,
        "lat": lat2d,
        "lon": lon2d,
        "precip": precip,
        "cloud": cloud,
        "storm": storm,
        "visibility": vis,
        "u500": u500,
        "v500": v500,
        "rain": rain_grid,
        "snow": snow_grid,
        "cold": cold_grid,
        "ice": ice_grid,
        "missing": missing,
    }


def _masked(values, mask):
    if values is None:
        return None
    return np.ma.masked_where(~mask, values)


def _xy_point(lat: float, lon: float, center_lat: float, center_lon: float) -> tuple[float, float]:
    x = float(_lon_delta(lon, center_lon)) * 111.320 * math.cos(math.radians(center_lat))
    y = (float(lat) - center_lat) * 110.574
    return x, y


def _line_in_view(points: list[tuple[float, float]], radius_km: float) -> bool:
    limit = radius_km * 1.2
    return any(math.hypot(x, y) <= limit for x, y in points)


def _draw_line_collection(ax, lines: list[list[tuple[float, float]]], *, color: str, linewidth: float, alpha: float, zorder: float, radius_km: float) -> None:
    for pts in lines:
        if len(pts) < 2 or not _line_in_view(pts, radius_km):
            continue
        ax.plot([point[0] for point in pts], [point[1] for point in pts], color=color, linewidth=linewidth, alpha=alpha, zorder=zorder)


def _basemap_status_text(stats: dict) -> str:
    status = stats.get("status")
    if status == "disabled":
        return "Подложка: базовая"
    if status == "missing_cache":
        warnings = stats.get("warnings") or ["локальный кэш не найден"]
        return "Подложка: " + str(warnings[0])
    if status != "ok":
        return "Подложка: локальный кэш не найден"
    warnings = stats.get("warnings") or []
    warning_text = f" · {warnings[0]}" if warnings else ""
    return (
        f"Подложка: Natural Earth {stats.get('resolution') or ''} · "
        f"города {int(stats.get('city_count') or 0)} · "
        f"реки {int(stats.get('river_count') or 0)} · "
        f"водоёмы {int(stats.get('water_count') or 0)} · "
        f"границы {int(stats.get('admin_count') or 0)}"
        f"{warning_text}"
    )


def _draw_legend(fig, ax) -> None:
    precip_text = "Осадки: APCP мм, если нет APCP — PRATE мм/ч · шкала 0.1 0.5 1 3 7 15+"
    cloud_text = "Облачность TCDC/TCC, %: 20 40 60 80 100 · серый прозрачный слой"
    extra_text = "⚡ гроза · ☔ дождь · ❄ снег/переохл. дождь · ≋ туман · стрелки: ветер AT500, м/с · VIS<10 км"
    fig.text(0.06, 0.065, precip_text, fontsize=9, color="#263238")
    fig.text(0.06, 0.04, cloud_text, fontsize=9, color="#263238")
    fig.text(0.06, 0.018, extra_text, fontsize=9, color="#263238")


def write_composite_map_png(data: dict, path: Path | None = None, *, pixel_size: int = 1280, basemap_overlay: dict | None = None, progress_callback: ProgressCallback | None = None) -> Path:
    _emit(progress_callback, stage="map_plot", message="Строю композитную карту")
    point: GeoPoint = data["point"]
    radius_km = float(data["radius_km"])
    if path is None:
        path = CACHE_DIR / f"map_{data['run'].date}_{data['run'].cycle}_f{data['lead_hour']:03d}_{int(time.time())}.png"
    dpi = 160
    fig_size = pixel_size / dpi
    fig, ax = plt.subplots(figsize=(fig_size, fig_size), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    basemap = _validate_basemap(str(data.get("basemap", MAP_BASEMAP_DEFAULT)))
    basemap_overlay = basemap_overlay if basemap_overlay is not None else local_basemap_overlay(point.lat, point.lon, radius_km, basemap)
    water = basemap_overlay.get("water_polygons") or []
    rivers = basemap_overlay.get("river_lines") or []
    roads = basemap_overlay.get("road_lines") or []
    admin_lines = basemap_overlay.get("admin_lines") or []
    coastline_lines = basemap_overlay.get("coastline_lines") or []
    cities = basemap_overlay.get("city_points") or []
    overlay_stats = basemap_overlay.get("stats") or {}
    data["overlay_summary"] = overlay_stats
    data["overlay_footer"] = _basemap_status_text(overlay_stats)
    for pts in water:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.fill(xs, ys, facecolor="#dff3fb", edgecolor="#9cc8d8", linewidth=0.5, alpha=0.9, zorder=1)

    x = data["x"]
    y = data["y"]
    mask = data["mask"]
    cloud = _masked(data["cloud"], mask)
    precip = _masked(data["precip"], mask)
    storm = _masked(data["storm"], mask)
    visibility = _masked(data["visibility"], mask)

    if cloud is not None:
        cloud_cmap = ListedColormap(["#ffffff00", "#e9ecef52", "#d0d5da66", "#aeb6bd75", "#858f988a"])
        cloud_norm = BoundaryNorm([0, 20, 40, 60, 80, 101], cloud_cmap.N)
        ax.pcolormesh(x, y, cloud, cmap=cloud_cmap, norm=cloud_norm, shading="auto", zorder=2)
    if precip is not None:
        precip_cmap = ListedColormap(["#ffffff00", "#dff6dd80", "#bceabb99", "#83d184ad", "#48b85ac2", "#189643d6", "#086b32e3"])
        precip_norm = BoundaryNorm([0, 0.1, 0.5, 1, 3, 7, 15, 999], precip_cmap.N)
        ax.pcolormesh(x, y, precip, cmap=precip_cmap, norm=precip_norm, shading="auto", zorder=3)

    _draw_line_collection(ax, coastline_lines, color="#6b8794", linewidth=0.75, alpha=0.82, zorder=4.1, radius_km=radius_km)
    _draw_line_collection(ax, water, color="#78b8cf", linewidth=0.55, alpha=0.92, zorder=4.2, radius_km=radius_km)
    _draw_line_collection(ax, admin_lines, color="#78909c", linewidth=0.55, alpha=0.55, zorder=4.35, radius_km=radius_km)
    _draw_line_collection(ax, roads, color="#b89f78", linewidth=0.75, alpha=0.72, zorder=4.5, radius_km=radius_km)
    _draw_line_collection(ax, rivers, color="#3f9ec0", linewidth=0.95, alpha=0.94, zorder=5, radius_km=radius_km)

    for ring in np.arange(MAP_RING_STEP_KM, radius_km + 0.1, MAP_RING_STEP_KM):
        circle = plt.Circle((0, 0), ring, fill=False, linewidth=0.8, linestyle="--", edgecolor="#90a4ae", alpha=0.8, zorder=8)
        ax.add_patch(circle)
        ax.text(ring / math.sqrt(2), ring / math.sqrt(2), f"{int(ring)} км", fontsize=8, color="#607d8b", ha="center", va="center", bbox={"boxstyle": "round,pad=0.12", "facecolor": "white", "edgecolor": "none", "alpha": 0.78}, zorder=9)

    if data["u500"] is not None and data["v500"] is not None:
        step = max(1, int(max(x.shape) / 9))
        uu = np.ma.masked_where(~mask, data["u500"])
        vv = np.ma.masked_where(~mask, data["v500"])
        ax.quiver(x[::step, ::step], y[::step, ::step], uu[::step, ::step], vv[::step, ::step], color="#263238", alpha=0.72, scale=360, width=0.0032, headwidth=3.2, zorder=6)

    if storm is not None:
        candidates = np.argwhere((np.asarray(storm.filled(0)) >= 2.0) & mask)
        for row, col in candidates[:: max(1, len(candidates) // 6 or 1)]:
            ax.text(float(x[row, col]), float(y[row, col]), "⚡", fontsize=16, color="#f9a825", ha="center", va="center", zorder=10)

    if visibility is not None or precip is not None:
        rows, cols = x.shape
        used = 0
        step = max(1, int(max(rows, cols) / 8))
        for row in range(0, rows, step):
            for col in range(0, cols, step):
                if not mask[row, col]:
                    continue
                text_parts: list[str] = []
                st = int(data["storm"][row, col]) if data["storm"] is not None else 0
                pr = float(data["precip"][row, col]) if data["precip"] is not None else None
                vis = float(data["visibility"][row, col]) if data["visibility"] is not None else None
                code = weather_code(pr, precipitation_code(bool(data["rain"][row, col]), bool(data["snow"][row, col]), bool(data["cold"][row, col]), bool(data["ice"][row, col])), st, vis)
                if code != DASH:
                    text_parts.append(weather_code_icon(code))
                if vis is not None and vis < 10.0:
                    text_parts.append(f"{vis:.1f} км" if vis < 2 else f"{vis:.0f} км")
                if not text_parts:
                    continue
                ax.text(float(x[row, col]), float(y[row, col]), "\n".join(text_parts), fontsize=8, color="#263238", ha="center", va="center", bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "#cfd8dc", "alpha": 0.86}, zorder=11)
                used += 1
                if used >= 22:
                    break
            if used >= 22:
                break

    ax.scatter([0], [0], marker="+", s=110, color="#d32f2f", linewidths=2.0, zorder=12)
    ax.text(2.5, -4.5, point.label, fontsize=8.5, color="#b71c1c", fontweight="bold", ha="left", va="top", bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "#ffcdd2", "alpha": 0.9}, zorder=13)
    for name, cx, cy in cities:
        if abs(cx) <= radius_km * 1.05 and abs(cy) <= radius_km * 1.05:
            ax.scatter([cx], [cy], s=11, color="#37474f", zorder=7.5)
            ax.text(cx + 2.0, cy + 1.2, name, fontsize=7.5, color="#263238", bbox={"boxstyle": "round,pad=0.08", "facecolor": "white", "edgecolor": "none", "alpha": 0.72}, zorder=7.5)

    ax.set_xlim(-radius_km * 1.08, radius_km * 1.08)
    ax.set_ylim(-radius_km * 1.08, radius_km * 1.08)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([-100, -50, 0, 50, 100])
    ax.set_yticks([-100, -50, 0, 50, 100])
    ax.grid(True, color="#eceff1", linewidth=0.7, zorder=0)
    ax.tick_params(labelsize=8, colors="#607d8b")
    ax.set_xlabel("км от центра", fontsize=9, color="#607d8b")
    ax.set_ylabel("км от центра", fontsize=9, color="#607d8b")
    title = f"GFS 0.25 · композитная карта · {point.label} · +{data['lead_hour']} ч"
    subtitle = f"{data['run'].date} {data['run'].cycle}Z · срок {data['valid_time']:%Y-%m-%d %H:%M} UTC · радиус {int(radius_km)} км"
    ax.set_title(title + "\n" + subtitle, fontsize=12, color="#263238", pad=12)
    _draw_legend(fig, ax)
    footer = "GFS 0.25 — модель, не радар и не наблюдения."
    overlay_footer = data.get("overlay_footer")
    if overlay_footer:
        footer += " " + str(overlay_footer) + "."
    if data["missing"]:
        footer += " Нет полей: " + ", ".join(sorted(data["missing"]))
    fig.text(0.06, 0.092, footer, fontsize=8, color="#78909c")
    fig.subplots_adjust(left=0.08, right=0.985, top=0.9, bottom=0.14)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    try:
        image = Image.open(path).convert("RGB")
        image.save(path, optimize=True)
    except Exception:
        pass
    _emit(progress_callback, stage="map_plot_done", message="Карта готова")
    return path


def write_composite_map_gif(frames: list[dict], path: Path | None = None, progress_callback: ProgressCallback | None = None) -> Path:
    if not frames:
        raise GfsProfileError("Нет кадров для анимации")
    if len(frames) > MAP_MAX_ANIMATION_FRAMES:
        raise GfsProfileError(f"Для Telegram-анимации допускается не больше {MAP_MAX_ANIMATION_FRAMES} кадров")
    if path is None:
        first = frames[0]
        path = CACHE_DIR / f"map_{first['run'].date}_{first['run'].cycle}_anim_{int(time.time())}.gif"
    basemap = _validate_basemap(str(frames[0].get("basemap", MAP_BASEMAP_DEFAULT)))
    point: GeoPoint = frames[0]["point"]
    basemap_overlay = local_basemap_overlay(point.lat, point.lon, float(frames[0]["radius_km"]), basemap)
    images: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, frame in enumerate(frames, start=1):
            _emit(progress_callback, stage="map_animation_frame", message=f"Строю кадр {index}/{len(frames)}", index=index, total=len(frames), lead_hour=frame["lead_hour"])
            png_path = Path(tmp) / f"frame_{index:03d}.png"
            write_composite_map_png(frame, png_path, pixel_size=960, basemap_overlay=basemap_overlay)
            image = Image.open(png_path).convert("P", palette=Image.ADAPTIVE, colors=96)
            images.append(image)
        images[0].save(path, save_all=True, append_images=images[1:], duration=650, loop=0, optimize=True)
    _emit(progress_callback, stage="map_animation_done", message="Анимация готова")
    return path


def build_composite_map_frames(run: GfsRun, leads: list[int], point: GeoPoint, radius_km: float = MAP_RADIUS_KM, basemap: str = MAP_BASEMAP_DEFAULT, progress_callback: ProgressCallback | None = None) -> list[dict]:
    frames: list[dict] = []
    total = len(leads)
    for index, lead in enumerate(leads, start=1):
        _emit(progress_callback, stage="map_step", message=f"Готовлю срок +{lead} ч", index=index, total=total, lead_hour=lead)
        frames.append(build_composite_map(run, lead, point, radius_km=radius_km, basemap=basemap, progress_callback=progress_callback))
    return frames
