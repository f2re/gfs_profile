from __future__ import annotations

import hashlib
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
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


GRIB_READ_KEYS = (
    "shortName",
    "typeOfLevel",
    "level",
    "stepType",
    "startStep",
    "endStep",
    "scaledValueOfFirstFixedSurface",
    "scaledValueOfSecondFixedSurface",
)


@dataclass(frozen=True)
class GribFieldSelector:
    """Unambiguous GRIB field selector.

    Names are matched against both the xarray variable name and
    ``GRIB_shortName``. Other attributes are normalized before comparison so
    cfgrib spellings such as ``entireAtmosphere`` and ``entire_atmosphere`` are
    equivalent. ``level`` uses native cfgrib coordinate units: hPa for
    ``isobaricInhPa`` and Pa for ``pressureFromGroundLayer``.
    """

    names: tuple[str, ...]
    type_of_level: tuple[str, ...] | None = None
    level: float | None = None
    step_types: tuple[str, ...] | None = None
    interval_hours: float | None = None
    prefer_shortest_interval: bool = False


@dataclass(frozen=True)
class SelectedGribField:
    data_array: Any
    variable_name: str
    short_name: str
    type_of_level: str
    level: float | None
    step_type: str
    start_step: float | None
    end_step: float | None

    @property
    def interval_hours(self) -> float | None:
        if self.start_step is None or self.end_step is None:
            return None
        return max(0.0, self.end_step - self.start_step)


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
                backend_kwargs={
                    "indexpath": str(index_dir / (_safe_token(path.stem) + ".idx")),
                    "errors": "ignore",
                    "read_keys": list(GRIB_READ_KEYS),
                },
            )
        )
    except Exception as exc:
        raise GfsProfileError(f"Ошибка чтения GFS subset через cfgrib: {exc}") from exc


def _norm(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _attr(data_array: Any, dataset: Any, key: str, default: object = "") -> object:
    attr_name = f"GRIB_{key}"
    if attr_name in getattr(data_array, "attrs", {}):
        return data_array.attrs[attr_name]
    if attr_name in getattr(dataset, "attrs", {}):
        return dataset.attrs[attr_name]
    return default


def _float_or_none(value: object) -> float | None:
    try:
        number = float(np.asarray(value).squeeze())
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _coord_type_of_level(data_array: Any) -> str:
    for name in (
        "isobaricInhPa",
        "isobaricInPa",
        "pressureFromGroundLayer",
        "heightAboveGround",
        "surface",
        "cloudCeiling",
        "lowCloudLayer",
        "middleCloudLayer",
        "highCloudLayer",
        "convectiveCloudLayer",
        "atmosphere",
        "entireAtmosphere",
    ):
        if name in getattr(data_array, "coords", {}):
            return name
    return ""


def _select_level(data_array: Any, type_of_level: str, requested_level: float | None) -> tuple[Any, float | None]:
    if requested_level is None:
        level = _float_or_none(getattr(data_array, "attrs", {}).get("GRIB_level"))
        return data_array, level

    requested = float(requested_level)
    coordinate_names: list[str] = []
    if type_of_level:
        coordinate_names.append(type_of_level)
    coordinate_names.extend(("isobaricInhPa", "isobaricInPa", "pressureFromGroundLayer", "heightAboveGround", "level"))

    for coord_name in dict.fromkeys(coordinate_names):
        if coord_name not in getattr(data_array, "coords", {}):
            continue
        try:
            values = np.asarray(data_array[coord_name].values, dtype=float)
            target = requested
            if coord_name == "isobaricInPa" and requested < 2000.0:
                target = requested * 100.0
            if coord_name == "isobaricInhPa" and requested > 2000.0:
                target = requested / 100.0
            tolerance = max(1e-6, abs(target) * 1e-4)
            if values.ndim == 0:
                value = float(values)
                if abs(value - target) <= tolerance:
                    return data_array, value
                continue
            index = int(np.nanargmin(np.abs(values - target)))
            value = float(values.flat[index])
            if abs(value - target) > tolerance:
                continue
            return data_array.sel({coord_name: value}, method="nearest"), value
        except Exception:
            continue

    attr_level = _float_or_none(getattr(data_array, "attrs", {}).get("GRIB_level"))
    if attr_level is not None:
        targets = (requested, requested * 100.0, requested / 100.0)
        if min(abs(attr_level - target) for target in targets) <= max(1e-6, abs(attr_level) * 1e-4):
            return data_array, attr_level
    return None, None


def _field_candidates(datasets: Iterable[Any], selector: GribFieldSelector) -> list[SelectedGribField]:
    names = {_norm(name) for name in selector.names}
    allowed_types = {_norm(value) for value in selector.type_of_level or ()}
    allowed_steps = {_norm(value) for value in selector.step_types or ()}
    candidates: list[SelectedGribField] = []

    for dataset in datasets:
        for variable_name, data_array in getattr(dataset, "data_vars", {}).items():
            short_name = str(_attr(data_array, dataset, "shortName", variable_name))
            if _norm(variable_name) not in names and _norm(short_name) not in names:
                continue
            type_of_level = str(_attr(data_array, dataset, "typeOfLevel", "") or _coord_type_of_level(data_array))
            if allowed_types and _norm(type_of_level) not in allowed_types:
                continue
            step_type = str(_attr(data_array, dataset, "stepType", ""))
            if allowed_steps and _norm(step_type) not in allowed_steps:
                continue

            selected, level = _select_level(data_array, type_of_level, selector.level)
            if selected is None:
                continue
            start_step = _float_or_none(_attr(selected, dataset, "startStep", None))
            end_step = _float_or_none(_attr(selected, dataset, "endStep", None))
            if end_step is None:
                step_coord = getattr(selected, "coords", {}).get("step")
                if step_coord is not None:
                    try:
                        end_step = float(np.asarray(step_coord / np.timedelta64(1, "h")).squeeze())
                    except Exception:
                        end_step = None

            candidates.append(
                SelectedGribField(
                    data_array=selected,
                    variable_name=str(variable_name),
                    short_name=short_name,
                    type_of_level=type_of_level,
                    level=level,
                    step_type=step_type,
                    start_step=start_step,
                    end_step=end_step,
                )
            )
    return candidates


def select_grib_field(datasets: Iterable[Any], selector: GribFieldSelector) -> SelectedGribField | None:
    candidates = _field_candidates(datasets, selector)
    if not candidates:
        return None

    if selector.interval_hours is not None:
        target = max(0.0, float(selector.interval_hours))
        with_interval = [item for item in candidates if item.interval_hours is not None]
        if with_interval:
            return sorted(
                with_interval,
                key=lambda item: (abs(float(item.interval_hours) - target), -(item.end_step or 0.0)),
            )[0]

    if selector.prefer_shortest_interval:
        with_interval = [item for item in candidates if item.interval_hours is not None and item.interval_hours > 0]
        if with_interval:
            return sorted(with_interval, key=lambda item: (float(item.interval_hours), -(item.end_step or 0.0)))[0]

    return sorted(
        candidates,
        key=lambda item: (0 if _norm(item.step_type) in {"instant", "instantaneous"} else 1, -(item.end_step or 0.0)),
    )[0]


def scalar_from_datasets(
    datasets: list[Any],
    names: tuple[str, ...],
    default: float | None = None,
    *,
    type_of_level: tuple[str, ...] | None = None,
    level: float | None = None,
    step_types: tuple[str, ...] | None = None,
    interval_hours: float | None = None,
    prefer_shortest_interval: bool = False,
) -> float | None:
    selected = select_grib_field(
        datasets,
        GribFieldSelector(
            names=names,
            type_of_level=type_of_level,
            level=level,
            step_types=step_types,
            interval_hours=interval_hours,
            prefer_shortest_interval=prefer_shortest_interval,
        ),
    )
    if selected is None:
        return default
    try:
        return float(np.asarray(selected.data_array.values).squeeze().flat[0])
    except Exception:
        return default


def bool_from_datasets(
    datasets: list[Any],
    names: tuple[str, ...],
    *,
    type_of_level: tuple[str, ...] | None = None,
    level: float | None = None,
    step_types: tuple[str, ...] | None = None,
) -> bool:
    value = scalar_from_datasets(
        datasets,
        names,
        default=0.0,
        type_of_level=type_of_level,
        level=level,
        step_types=step_types,
    )
    return bool(value is not None and value >= 0.5)
