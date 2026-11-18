"""Regional NOMADS input, exact-lead selection and strict GRIB validation."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import numpy as np
import requests

from gfs_core import (CACHE_DIR, NOMADS_BASE, REQUEST_TIMEOUT, GfsProfileError,
                      GfsRun, latest_available_run_for_lead, now_utc,
                      run_dir, run_file_name, source_idx_url)
from icing_config import (ICING_LEVELS_HPA, ICING_MAX_GRID_POINTS, IcingRequest,
                          validate_icing_request)

ATMOSPHERIC_VARIABLES = ("TMP", "SPFH", "HGT", "CLWMR")
SURFACE_VARIABLES = ("PRES", "HGT")
RAW_SCHEMA = "icing_raw_v1"


class IcingCancelled(GfsProfileError):
    """Cooperative cancellation; the worker retains its gate until it exits."""


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise IcingCancelled("Расчёт карты обледенения отменён")


def _emit(callback, stage: str, message: str, **data) -> None:
    if callback:
        callback({"stage": stage, "message": message, **data})


def missing_index_fields(text: str, run: GfsRun) -> list[str]:
    """An index must contain every required field at the selected model cycle."""
    records = set()
    stamp = f"d={run.date}{run.cycle}"
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) >= 6 and parts[2] == stamp:
            records.add((parts[3], parts[4]))
    required = [(var, f"{p} mb") for p in ICING_LEVELS_HPA for var in ATMOSPHERIC_VARIABLES]
    required += [(var, "surface") for var in SURFACE_VARIABLES]
    return [f"{var} {level}" for var, level in required if (var, level) not in records]


def read_icing_index(run: GfsRun, lead: int, cancel_event=None) -> str:
    check_cancel(cancel_event)
    try:
        response = requests.get(source_idx_url(run.date, run.cycle, lead), timeout=(12, max(20, REQUEST_TIMEOUT)))
        response.raise_for_status()
        text = response.text
        if "<html" in text[:500].lower() or not text.strip():
            raise GfsProfileError("NOMADS вернул ошибку вместо индекса GFS")
    except requests.RequestException as exc:
        raise GfsProfileError(f"Индекс GFS +{lead} ч недоступен: {exc}") from exc
    check_cancel(cancel_event)
    missing = missing_index_fields(text, run)
    if missing:
        raise GfsProfileError("В индексе срока отсутствуют поля обледенения: " + ", ".join(missing[:5]))
    return text


def select_icing_run(request: IcingRequest, *, progress_callback=None,
                     cancel_event=None, run_selector: Callable | None = None) -> tuple[GfsRun, str]:
    """Use the shared selector, then check this product's entire field matrix."""
    r = validate_icing_request(request)
    selector = run_selector or latest_available_run_for_lead
    reference = now_utc()
    oldest = reference - timedelta(days=3)
    last_error = ""
    for _ in range(12):
        check_cancel(cancel_event)
        run = r.run or selector(r.lead, reference_time=reference)
        if run.run_datetime_utc < oldest and r.run is None:
            break
        _emit(progress_callback, "check", f"Проверяю f{r.lead:03d}.idx · {run.date} {run.cycle} UTC")
        try:
            return run, read_icing_index(run, r.lead, cancel_event)
        except IcingCancelled:
            raise
        except GfsProfileError as exc:
            last_error = str(exc)
            if r.run is not None:
                raise
            reference = run.run_datetime_utc - timedelta(seconds=1)
    raise GfsProfileError("Не найден цикл с полным набором полей обледенения. " + last_error)


def icing_subset_url(run: GfsRun, lead: int, bbox, *, surface: bool = False) -> str:
    west, east, south, north = bbox
    query = {"file": run_file_name(run.cycle, lead), "dir": run_dir(run.date, run.cycle),
             "subregion": "", "leftlon": f"{west:.3f}", "rightlon": f"{east:.3f}",
             "bottomlat": f"{south:.3f}", "toplat": f"{north:.3f}"}
    for variable in SURFACE_VARIABLES if surface else ATMOSPHERIC_VARIABLES:
        query[f"var_{variable}"] = "on"
    if surface:
        query["lev_surface"] = "on"
    else:
        query.update({f"lev_{p}_mb": "on" for p in ICING_LEVELS_HPA})
    return f"{NOMADS_BASE}/cgi-bin/filter_gfs_0p25_1hr.pl?{urlencode(query)}"


def raw_cache_key(run: GfsRun, lead: int, bbox) -> str:
    definition = {"bbox": bbox, "levels": ICING_LEVELS_HPA, "vars": ATMOSPHERIC_VARIABLES,
                  "surface": SURFACE_VARIABLES, "source": NOMADS_BASE, "schema": RAW_SCHEMA}
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:20]
    return f"{RAW_SCHEMA}_{run.date}_{run.cycle}_f{lead:03d}_{digest}"


def validate_grib_file(path: Path) -> None:
    """Check every GRIB2 message, including lengths and end markers."""
    try:
        total = path.stat().st_size
        offset = 0
        count = 0
        with path.open("rb") as stream:
            while offset < total:
                stream.seek(offset)
                header = stream.read(16)
                if len(header) != 16 or header[:4] != b"GRIB" or header[7] != 2:
                    raise ValueError("нет заголовка GRIB2")
                length = int.from_bytes(header[8:16], "big")
                if length < 20 or offset + length > total:
                    raise ValueError("оборванное GRIB2-сообщение")
                stream.seek(offset + length - 4)
                if stream.read(4) != b"7777":
                    raise ValueError("нет окончания GRIB2")
                offset += length
                count += 1
            if count == 0:
                raise ValueError("пустой GRIB2")
    except (OSError, ValueError) as exc:
        raise GfsProfileError(f"Повреждён файл GFS: {exc}") from exc


def _verify_manifest(provenance: Path, paths: tuple[Path, Path]) -> None:
    # Existing manually seeded files still undergo strict time/grid/unit decoding.
    if not provenance.exists():
        return
    try:
        metadata = json.loads(provenance.read_text(encoding="utf-8"))
        records = {item["name"]: item for item in metadata["files"]}
        for path in paths:
            item = records[path.name]
            if (path.stat().st_size != item["bytes"] or
                    hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]):
                raise ValueError("контрольная сумма GRIB изменилась")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GfsProfileError(f"Повреждён кэш карты обледенения: {exc}") from exc


@contextmanager
def cache_lock(key: str, *, cancel_event=None):
    """Linux flock prevents two messenger workers from replacing the same file."""
    import fcntl
    directory = CACHE_DIR / "icing_locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{key}.lock").open("a+b") as lock:
        started = time.monotonic()
        while True:
            check_cancel(cancel_event)
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started > 600:
                    raise GfsProfileError("Истекло время ожидания загрузки карты")
                if cancel_event is not None:
                    cancel_event.wait(.1)
                else:
                    time.sleep(.1)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _download_once(url: str, path: Path, *, progress_callback=None, cancel_event=None) -> None:
    check_cancel(cancel_event)
    fd, temporary = tempfile.mkstemp(prefix=path.stem + "_", suffix=".part", dir=path.parent)
    partial = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            with requests.get(url, stream=True, timeout=(15, max(120, REQUEST_TIMEOUT))) as response:
                response.raise_for_status()
                if "text/html" in response.headers.get("content-type", "").lower():
                    raise GfsProfileError("NOMADS вернул HTML вместо GRIB2 обледенения")
                downloaded, last_emit = 0, 0.0
                for chunk in response.iter_content(chunk_size=131072):
                    check_cancel(cancel_event)
                    if not chunk:
                        continue
                    stream.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > 100_000_000:
                        raise GfsProfileError("Ответ NOMADS превышает размер региональной выборки")
                    if time.monotonic() - last_emit > 1:
                        _emit(progress_callback, "download", f"Загружаю поля: {downloaded/1_000_000:.1f} МБ")
                        last_emit = time.monotonic()
        validate_grib_file(partial)
        check_cancel(cancel_event)
        partial.replace(path)
    except requests.RequestException as exc:
        raise GfsProfileError(f"Не удалось загрузить поля GFS: {exc}") from exc
    finally:
        partial.unlink(missing_ok=True)


def _download(url: str, path: Path, *, progress_callback=None, cancel_event=None) -> None:
    for attempt in range(2):
        try:
            _download_once(url, path, progress_callback=progress_callback, cancel_event=cancel_event)
            return
        except IcingCancelled:
            raise
        except GfsProfileError as exc:
            cause = exc.__cause__
            response = getattr(cause, "response", None)
            status = getattr(response, "status_code", None)
            transient = isinstance(cause, requests.RequestException) and (status is None or status == 429 or status >= 500)
            if attempt or not transient:
                raise
            _emit(progress_callback, "download", "Повторяю загрузку после временной ошибки NOMADS")
            if cancel_event is not None:
                cancel_event.wait(1)
            else:
                time.sleep(1)
            check_cancel(cancel_event)


@dataclass(frozen=True)
class IcingInput:
    lat: np.ndarray
    lon: np.ndarray
    fields: dict[str, np.ndarray]
    run: GfsRun
    lead: int
    paths: tuple[Path, Path]


def _check_times(ds, run: GfsRun, lead: int) -> None:
    expected = {
        "time": np.datetime64(run.run_datetime_utc.replace(tzinfo=None), "ns"),
        "valid_time": np.datetime64((run.run_datetime_utc + timedelta(hours=lead)).replace(tzinfo=None), "ns"),
    }
    for key, value in expected.items():
        if key not in ds.coords or ds[key].size != 1 or ds[key].values.astype("datetime64[ns]").item() != value.item():
            raise GfsProfileError(f"GRIB содержит другой цикл или срок ({key})")
    if "step" not in ds.coords or ds["step"].size != 1:
        raise GfsProfileError("В GRIB нет однозначного срока прогноза")
    if ds["step"].values.astype("timedelta64[h]").item() != timedelta(hours=lead):
        raise GfsProfileError("В GRIB указан другой срок прогноза")


def read_icing_grib(atmosphere_path: Path, surface_path: Path, run: GfsRun,
                    lead: int, bbox, *, cancel_event=None) -> IcingInput:
    """Decode using xarray/cfgrib with strict type-of-level filters and no index races."""
    import xarray as xr
    atmosphere_path, surface_path = Path(atmosphere_path), Path(surface_path)
    for path in (atmosphere_path, surface_path):
        validate_grib_file(path)
    result = {}
    reference = None
    with tempfile.TemporaryDirectory(prefix="icing_grib_") as directory:
        for surface, path in ((False, atmosphere_path), (True, surface_path)):
            check_cancel(cancel_event)
            try:
                with xr.open_dataset(str(path), engine="cfgrib", backend_kwargs={
                    "indexpath": str(Path(directory) / ("surface.idx" if surface else "levels.idx")),
                    "errors": "raise", "filter_by_keys": {"typeOfLevel": "surface" if surface else "isobaricInhPa"},
                }) as ds:
                    _check_times(ds, run, lead)
                    if "latitude" not in ds.coords or "longitude" not in ds.coords:
                        raise GfsProfileError("В GRIB отсутствует координатная сетка")
                    latitude = np.asarray(ds["latitude"].values, dtype=float)
                    longitude = (np.asarray(ds["longitude"].values, dtype=float) + 180) % 360 - 180
                    if latitude.ndim != 1 or longitude.ndim != 1:
                        raise GfsProfileError("Ожидалась регулярная широтно-долготная сетка GFS")
                    iy, ix = np.argsort(-latitude), np.argsort(longitude)
                    latitude, longitude = latitude[iy], longitude[ix]
                    if len(latitude) < 2 or len(longitude) < 2 or len(latitude)*len(longitude) > ICING_MAX_GRID_POINTS:
                        raise GfsProfileError("Размер GRIB не соответствует региональной карте")
                    west, east, south, north = bbox
                    if (longitude[0] < west-.251 or longitude[-1] > east+.251 or
                        latitude[-1] < south-.251 or latitude[0] > north+.251 or
                        longitude[0] > west+.251 or longitude[-1] < east-.251 or
                        latitude[-1] > south+.251 or latitude[0] < north-.251 or
                        not np.allclose(np.diff(longitude), .25, atol=1e-6) or
                        not np.allclose(np.diff(latitude), -.25, atol=1e-6)):
                        raise GfsProfileError("Координаты GRIB не соответствуют району и сетке 0,25°")
                    if reference is None:
                        reference = (latitude, longitude)
                    elif not (np.array_equal(reference[0], latitude) and np.array_equal(reference[1], longitude)):
                        raise GfsProfileError("Сетки приземных и высотных полей различаются")
                    variables = ({"sp": ("surface_pressure_pa", "Pa"), "orog": ("surface_height_m", "m")}
                                 if surface else {"t": ("temperature_k", "K"), "q": ("specific_humidity", "kg kg**-1"),
                                                  "gh": ("height_gpm", "gpm"), "clwmr": ("cloud_water_kgkg", "kg kg**-1")})
                    if not surface:
                        if "isobaricInhPa" not in ds.coords or not set(ICING_LEVELS_HPA).issubset(set(ds.isobaricInhPa.values)):
                            raise GfsProfileError("В GRIB отсутствуют требуемые изобарические уровни")
                        ds = ds.sel(isobaricInhPa=list(ICING_LEVELS_HPA))
                    for short, (name, units) in variables.items():
                        if short not in ds:
                            raise GfsProfileError(f"В GRIB отсутствует поле {short}")
                        if ds[short].attrs.get("units") != units:
                            raise GfsProfileError(f"Неподдерживаемые единицы {short}: {ds[short].attrs.get('units')}")
                        dims = (("latitude", "longitude") if surface else ("isobaricInhPa", "latitude", "longitude"))
                        value = np.asarray(ds[short].transpose(*dims).values, dtype=float)
                        result[name] = np.take(np.take(value, iy, axis=-2), ix, axis=-1)
            except (GfsProfileError, IcingCancelled):
                raise
            except Exception as exc:
                raise GfsProfileError(f"Не удалось прочитать GRIB2 обледенения: {exc}") from exc
    check_cancel(cancel_event)
    result["pressure_hpa"] = np.asarray(ICING_LEVELS_HPA, dtype=float)
    longitude, latitude = np.meshgrid(reference[1], reference[0])
    return IcingInput(latitude, longitude, result, run, lead, (atmosphere_path, surface_path))


def load_icing_input(request: IcingRequest, *, progress_callback=None, cancel_event=None) -> IcingInput:
    r = validate_icing_request(request)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    run, index = r.run, None
    if run is None:
        run, index = select_icing_run(r, progress_callback=progress_callback, cancel_event=cancel_event)
    key = raw_cache_key(run, r.lead, r.bbox)
    atmosphere = CACHE_DIR / f"{key}_levels.grib2"
    surface = CACHE_DIR / f"{key}_surface.grib2"
    provenance = CACHE_DIR / f"{key}.json"
    paths = (atmosphere, surface)
    with cache_lock(key, cancel_event=cancel_event):
        if all(path.exists() for path in paths):
            try:
                _emit(progress_callback, "cache", "Проверяю региональные данные в кэше")
                _verify_manifest(provenance, paths)
                return read_icing_grib(*paths, run, r.lead, r.bbox, cancel_event=cancel_event)
            except IcingCancelled:
                raise
            except GfsProfileError:
                for path in (*paths, provenance):
                    path.unlink(missing_ok=True)
        if index is None:
            _emit(progress_callback, "check", f"Проверяю f{r.lead:03d}.idx")
            index = read_icing_index(run, r.lead, cancel_event)
        _emit(progress_callback, "run", f"GFS {run.date} {run.cycle} UTC · +{r.lead} ч",
              run_date=run.date, run_cycle=run.cycle)
        urls = [icing_subset_url(run, r.lead, r.bbox, surface=is_surface) for is_surface in (False, True)]
        try:
            for path, url in zip(paths, urls):
                if not path.exists():
                    _download(url, path, progress_callback=progress_callback, cancel_event=cancel_event)
            _emit(progress_callback, "parse", "Читаю GRIB2 и проверяю цикл, срок, уровни и единицы")
            inputs = read_icing_grib(*paths, run, r.lead, r.bbox, cancel_event=cancel_event)
            metadata = {"schema": RAW_SCHEMA, "run_date": run.date, "run_cycle": run.cycle,
                        "lead": r.lead, "bbox": r.bbox, "levels_hpa": ICING_LEVELS_HPA,
                        "source_urls": urls, "retrieved_utc": now_utc().isoformat(),
                        "source_index_sha256": hashlib.sha256(index.encode()).hexdigest(),
                        "files": [{"name": p.name, "bytes": p.stat().st_size,
                                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
            temporary = provenance.with_suffix(".json.part")
            temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(provenance)
            return inputs
        except BaseException:
            for path in (*paths, provenance, provenance.with_suffix(".json.part")):
                path.unlink(missing_ok=True)
            raise
