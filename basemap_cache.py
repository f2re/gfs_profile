from __future__ import annotations

import json
import math
import os
import time
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable

import requests

from gfs_core import CACHE_DIR

PROJECT_ROOT = Path(__file__).resolve().parent
BASEMAP_SCHEMA_VERSION = 1
DEFAULT_BASEMAP_RESOLUTION = "10m"
BASEMAP_DOWNLOAD_TIMEOUT = int(os.getenv("MAP_BASEMAP_DOWNLOAD_TIMEOUT", "30"))
NATURAL_EARTH_BASE_URL = "https://naturalearth.s3.amazonaws.com"
VALID_RESOLUTIONS = {"10m", "50m", "110m"}

LAYER_SPECS: dict[str, dict[str, object]] = {
    "coastline": {"category": "physical", "name": "coastline", "required": True, "polygon": False},
    "lakes": {"category": "physical", "name": "lakes", "required": True, "polygon": True},
    "rivers": {"category": "physical", "name": "rivers_lake_centerlines", "required": True, "polygon": False},
    "admin0": {"category": "cultural", "name": "admin_0_boundary_lines_land", "required": True, "polygon": False},
    "admin1": {"category": "cultural", "name": "admin_1_states_provinces_lines", "required": False, "polygon": False},
    "places": {"category": "cultural", "name": "populated_places", "required": True, "polygon": False},
    "roads": {"category": "cultural", "name": "roads", "required": False, "polygon": False},
}
MODE_LAYERS = {
    "basic": (),
    "water": ("coastline", "lakes", "rivers"),
    "places": ("coastline", "lakes", "rivers", "admin0", "admin1", "places"),
    "roads": ("coastline", "lakes", "rivers", "admin0", "admin1", "places", "roads"),
}


@dataclass(frozen=True)
class LayerStatus:
    layer: str
    resolution: str
    ok: bool
    path: Path | None = None
    error: str | None = None
    downloaded: bool = False


@dataclass(frozen=True)
class BasemapCacheStatus:
    resolution: str
    ready: bool
    data_dir: Path
    manifest_path: Path
    layers: dict[str, LayerStatus]
    missing_layers: list[str]
    failed_layers: dict[str, str]


@dataclass(frozen=True)
class BasemapFeature:
    kind: str
    name: str | None
    points: list[tuple[float, float]]
    is_polygon: bool = False
    rank: int | None = None
    population: int | None = None
    capital_rank: int | None = None


def basemap_resolution(value: str | None = None) -> str:
    resolution = (value or os.getenv("MAP_BASEMAP_RESOLUTION") or DEFAULT_BASEMAP_RESOLUTION).strip().lower()
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError("MAP_BASEMAP_RESOLUTION must be 10m, 50m or 110m")
    return resolution


def basemap_data_dir() -> Path:
    return Path(os.getenv("MAP_BASEMAP_DIR") or (PROJECT_ROOT / "data" / "basemap"))


def basemap_manifest_path(resolution: str) -> Path:
    return basemap_data_dir() / "natural_earth" / basemap_resolution(resolution) / "manifest.json"


def _resolution_dir(resolution: str) -> Path:
    return basemap_manifest_path(resolution).parent


def _layer_spec(layer: str) -> dict[str, object]:
    try:
        return LAYER_SPECS[layer]
    except KeyError as exc:
        raise ValueError(f"Unknown basemap layer: {layer}") from exc


def _layer_ne_name(layer: str) -> str:
    return str(_layer_spec(layer)["name"])


def _layer_base_path(layer: str, resolution: str) -> Path:
    return _resolution_dir(resolution) / f"ne_{basemap_resolution(resolution)}_{_layer_ne_name(layer)}"


def _layer_shp_path(layer: str, resolution: str) -> Path:
    return _layer_base_path(layer, resolution).with_suffix(".shp")


def _layer_files_present(layer: str, resolution: str) -> bool:
    base = _layer_base_path(layer, resolution)
    return all(base.with_suffix(suffix).exists() for suffix in (".shp", ".shx", ".dbf"))


def _layer_url(layer: str, resolution: str) -> str:
    resolution = basemap_resolution(resolution)
    spec = _layer_spec(layer)
    category = str(spec["category"])
    name = str(spec["name"])
    return f"{NATURAL_EARTH_BASE_URL}/{resolution}_{category}/ne_{resolution}_{name}.zip"


def _manifest_payload(resolution: str, statuses: dict[str, LayerStatus]) -> dict:
    files = {
        layer: [path.name for path in sorted(_resolution_dir(resolution).glob(f"ne_{resolution}_{_layer_ne_name(layer)}.*"))]
        for layer, status in statuses.items()
        if status.ok
    }
    failed_layers = {layer: status.error for layer, status in statuses.items() if not status.ok and status.error}
    return {
        "schema_version": BASEMAP_SCHEMA_VERSION,
        "source": "Natural Earth",
        "source_url": NATURAL_EARTH_BASE_URL,
        "resolution": resolution,
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
        "failed_layers": failed_layers,
    }


def _read_manifest(resolution: str) -> dict | None:
    path = basemap_manifest_path(resolution)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema_version") != BASEMAP_SCHEMA_VERSION:
        return None
    if payload.get("resolution") != basemap_resolution(resolution):
        return None
    return payload


def _layer_status(layer: str, resolution: str) -> LayerStatus:
    path = _layer_shp_path(layer, resolution)
    if _layer_files_present(layer, resolution):
        return LayerStatus(layer, resolution, True, path=path)
    return LayerStatus(layer, resolution, False, path=path, error="missing files")


def is_basemap_cache_ready(resolution: str | None = None) -> bool:
    resolution = basemap_resolution(resolution)
    if _read_manifest(resolution) is None:
        return False
    return all(_layer_files_present(layer, resolution) for layer, spec in LAYER_SPECS.items() if bool(spec.get("required")))


def download_basemap_layer(layer: str, resolution: str | None = None) -> LayerStatus:
    resolution = basemap_resolution(resolution)
    _layer_spec(layer)
    target_dir = _resolution_dir(resolution)
    target_dir.mkdir(parents=True, exist_ok=True)
    url = _layer_url(layer, resolution)
    try:
        response = requests.get(url, timeout=BASEMAP_DOWNLOAD_TIMEOUT, stream=True)
        response.raise_for_status()
        with NamedTemporaryFile(prefix=f"ne_{resolution}_{layer}_", suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    tmp.write(chunk)
        try:
            with zipfile.ZipFile(tmp_path) as archive:
                archive.extractall(target_dir)
        finally:
            tmp_path.unlink(missing_ok=True)
        if not _layer_files_present(layer, resolution):
            return LayerStatus(layer, resolution, False, path=_layer_shp_path(layer, resolution), error="downloaded archive does not contain shp/shx/dbf")
        return LayerStatus(layer, resolution, True, path=_layer_shp_path(layer, resolution), downloaded=True)
    except Exception as exc:
        return LayerStatus(layer, resolution, False, path=_layer_shp_path(layer, resolution), error=f"{type(exc).__name__}: {exc}")


def ensure_basemap_cache(resolution: str | None = None, *, force: bool = False) -> BasemapCacheStatus:
    resolution = basemap_resolution(resolution)
    target_dir = _resolution_dir(resolution)
    target_dir.mkdir(parents=True, exist_ok=True)
    statuses: dict[str, LayerStatus] = {}
    for layer in LAYER_SPECS:
        if not force and _layer_files_present(layer, resolution):
            statuses[layer] = _layer_status(layer, resolution)
            continue
        statuses[layer] = download_basemap_layer(layer, resolution)
    manifest_path = basemap_manifest_path(resolution)
    try:
        manifest_path.write_text(json.dumps(_manifest_payload(resolution, statuses), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    missing_required = [layer for layer, spec in LAYER_SPECS.items() if bool(spec.get("required")) and not statuses[layer].ok]
    failed_layers = {layer: status.error or "failed" for layer, status in statuses.items() if not status.ok}
    return BasemapCacheStatus(
        resolution=resolution,
        ready=not missing_required,
        data_dir=target_dir,
        manifest_path=manifest_path,
        layers=statuses,
        missing_layers=missing_required,
        failed_layers=failed_layers,
    )


def check_basemap_cache(resolution: str | None = None) -> BasemapCacheStatus:
    resolution = basemap_resolution(resolution)
    statuses = {layer: _layer_status(layer, resolution) for layer in LAYER_SPECS}
    missing_required = [layer for layer, spec in LAYER_SPECS.items() if bool(spec.get("required")) and not statuses[layer].ok]
    failed_layers = {layer: status.error or "missing" for layer, status in statuses.items() if not status.ok}
    manifest_ok = _read_manifest(resolution) is not None
    return BasemapCacheStatus(
        resolution=resolution,
        ready=manifest_ok and not missing_required,
        data_dir=_resolution_dir(resolution),
        manifest_path=basemap_manifest_path(resolution),
        layers=statuses,
        missing_layers=missing_required,
        failed_layers=failed_layers,
    )


def _lon180(value: float) -> float:
    return ((float(value) + 180.0) % 360.0) - 180.0


def _lon_delta(lon: float, center_lon: float) -> float:
    return ((float(lon) - _lon180(center_lon) + 180.0) % 360.0) - 180.0


def _xy_point(lat: float, lon: float, center_lat: float, center_lon: float) -> tuple[float, float]:
    x = _lon_delta(lon, center_lon) * 111.320 * math.cos(math.radians(center_lat))
    y = (float(lat) - center_lat) * 110.574
    return x, y


def _area_box_from_radius(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lon = _lon180(lon)
    dlat = radius_km / 110.574
    cos_lat = max(0.08, abs(math.cos(math.radians(lat))))
    dlon = radius_km / (111.320 * cos_lat)
    return round(max(-90.0, lat - dlat), 3), round(min(90.0, lat + dlat), 3), round(max(-180.0, lon - dlon), 3), round(min(180.0, lon + dlon), 3)


def _bbox_intersects(a: Iterable[float], b: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = [float(v) for v in a]
    south, north, west, east = b
    return max_lon >= west and min_lon <= east and max_lat >= south and min_lat <= north


def _point_in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    south, north, west, east = bbox
    return south <= lat <= north and west <= lon <= east


def _shape_intersects_bbox(shape, bbox: tuple[float, float, float, float]) -> bool:
    if getattr(shape, "bbox", None):
        return _bbox_intersects(shape.bbox, bbox)
    return any(_point_in_bbox(float(lat), float(lon), bbox) for lon, lat in getattr(shape, "points", []))


def _shape_parts(shape) -> list[list[tuple[float, float]]]:
    points = [(float(lon), float(lat)) for lon, lat in getattr(shape, "points", [])]
    if not points:
        return []
    parts = list(getattr(shape, "parts", []) or [0]) + [len(points)]
    result: list[list[tuple[float, float]]] = []
    for start, end in zip(parts, parts[1:]):
        segment = points[int(start):int(end)]
        if segment:
            result.append(segment)
    return result


def _record_dict(shape_record) -> dict:
    try:
        return dict(shape_record.record.as_dict())
    except Exception:
        return {}


def _int_field(record: dict, names: tuple[str, ...]) -> int | None:
    for name in names:
        value = record.get(name)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _feature_name(record: dict) -> str | None:
    for key in ("NAME_RU", "NAME", "NAMEASCII", "name"):
        value = record.get(key)
        if value:
            return str(value)
    return None


@lru_cache(maxsize=64)
def _cached_layer_records(data_dir: str, layer_name: str, resolution: str) -> tuple:
    path = Path(data_dir) / "natural_earth" / resolution / f"ne_{resolution}_{_layer_ne_name(layer_name)}.shp"
    import shapefile

    is_polygon = bool(_layer_spec(layer_name).get("polygon"))
    rows = []
    with shapefile.Reader(str(path)) as reader:
        for shape_record in reader.iterShapeRecords():
            shape = shape_record.shape
            record = _record_dict(shape_record)
            rows.append((
                tuple(float(v) for v in getattr(shape, "bbox", []) or ()),
                tuple(tuple(point for point in part) for part in _shape_parts(shape)),
                _feature_name(record),
                _int_field(record, ("SCALERANK", "scalerank", "RANK_MAX", "RANK_MIN")),
                _int_field(record, ("POP_MAX", "POP_MIN", "POP_EST")),
                _int_field(record, ("ADM0CAP",)),
                is_polygon,
            ))
    return tuple(rows)


def iter_layer_features(layer_name: str, bbox: tuple[float, float, float, float], resolution: str | None = None) -> Iterable[BasemapFeature]:
    resolution = basemap_resolution(resolution)
    if not _layer_files_present(layer_name, resolution):
        return

    for shape_bbox, parts, name, rank, population, capital_rank, is_polygon in _cached_layer_records(str(basemap_data_dir()), layer_name, resolution):
        if shape_bbox and not _bbox_intersects(shape_bbox, bbox):
            continue
        for part in parts:
            if len(part) >= 1:
                yield BasemapFeature(layer_name, name, list(part), is_polygon=is_polygon, rank=rank, population=population, capital_rank=capital_rank)


def _xy_line_in_view(points: list[tuple[float, float]], radius_km: float) -> bool:
    limit = radius_km * 1.25
    return any(math.hypot(x, y) <= limit for x, y in points)


def _feature_xy(feature: BasemapFeature, center_lat: float, center_lon: float) -> list[tuple[float, float]]:
    return [_xy_point(lat, lon, center_lat, center_lon) for lon, lat in feature.points]


def _empty_overlay(mode: str, resolution: str, status: str, warnings: list[str] | None = None, missing_layers: list[str] | None = None) -> dict:
    warnings = warnings or []
    missing_layers = missing_layers or []
    return {
        "water_polygons": [],
        "river_lines": [],
        "road_lines": [],
        "admin_lines": [],
        "coastline_lines": [],
        "city_points": [],
        "stats": {
            "source": "Natural Earth",
            "resolution": resolution,
            "basemap": mode,
            "status": status,
            "warnings": warnings,
            "missing_layers": missing_layers,
            "city_count": 0,
            "water_count": 0,
            "river_count": 0,
            "road_count": 0,
            "admin_count": 0,
            "coastline_count": 0,
        },
    }


def local_basemap_overlay(center_lat: float, center_lon: float, radius_km: float, basemap_mode: str, resolution: str | None = None) -> dict:
    mode = str(basemap_mode or "places").lower()
    resolution = basemap_resolution(resolution)
    if mode == "basic":
        return _empty_overlay(mode, resolution, "disabled")
    if mode not in MODE_LAYERS:
        raise ValueError(f"Unknown basemap mode: {basemap_mode}")

    if not is_basemap_cache_ready(resolution) and os.getenv("MAP_BASEMAP_AUTO_DOWNLOAD", "1").strip().lower() not in {"0", "false", "no", "off"}:
        ensure_basemap_cache(resolution)
    status = check_basemap_cache(resolution)
    requested_layers = MODE_LAYERS[mode]
    missing_layers = [layer for layer in requested_layers if not _layer_files_present(layer, resolution)]
    warnings = [f"слой {layer} отсутствует в локальном кэше" for layer in missing_layers]
    if not status.ready:
        return _empty_overlay(mode, resolution, "missing_cache", ["локальный кэш не найден"] + warnings, missing_layers)

    bbox = _area_box_from_radius(center_lat, center_lon, radius_km * 1.25)
    water_polygons: list[list[tuple[float, float]]] = []
    river_lines: list[list[tuple[float, float]]] = []
    road_lines: list[list[tuple[float, float]]] = []
    admin_lines: list[list[tuple[float, float]]] = []
    coastline_lines: list[list[tuple[float, float]]] = []
    city_candidates: list[tuple[str, float, float, int, int, float]] = []

    for layer in requested_layers:
        if layer in missing_layers:
            continue
        for feature in iter_layer_features(layer, bbox, resolution):
            xy = _feature_xy(feature, center_lat, center_lon)
            if not xy:
                continue
            if layer == "places":
                x, y = xy[0]
                distance = math.hypot(x, y)
                if distance <= radius_km * 1.25 and feature.name:
                    scalerank = feature.rank if feature.rank is not None else 99
                    pop = feature.population if feature.population is not None else 0
                    capital = feature.capital_rank if feature.capital_rank is not None else 0
                    city_candidates.append((feature.name, x, y, scalerank, pop, distance - capital * 1000.0))
                continue
            if len(xy) < 2 or not _xy_line_in_view(xy, radius_km):
                continue
            if layer == "lakes":
                if len(xy) >= 3:
                    water_polygons.append(xy)
            elif layer == "rivers":
                river_lines.append(xy)
            elif layer == "roads":
                road_lines.append(xy)
            elif layer in {"admin0", "admin1"}:
                admin_lines.append(xy)
            elif layer == "coastline":
                coastline_lines.append(xy)

    city_candidates.sort(key=lambda item: (item[3], -item[4], abs(item[5])))
    city_points = [(name, x, y) for name, x, y, _rank, _pop, _distance in city_candidates[:12]]
    stats = {
        "source": "Natural Earth",
        "resolution": resolution,
        "basemap": mode,
        "status": "ok",
        "warnings": warnings,
        "missing_layers": missing_layers,
        "city_count": len(city_points),
        "water_count": len(water_polygons),
        "river_count": len(river_lines),
        "road_count": len(road_lines),
        "admin_count": len(admin_lines),
        "coastline_count": len(coastline_lines),
    }
    return {
        "water_polygons": water_polygons,
        "river_lines": river_lines,
        "road_lines": road_lines,
        "admin_lines": admin_lines,
        "coastline_lines": coastline_lines,
        "city_points": city_points,
        "stats": stats,
    }
