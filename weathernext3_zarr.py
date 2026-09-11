"""Bounded, lazy WN3 upper-air access. BigQuery remains the surface provider.

Raw Zarr uses a level coordinate (or flattened level-suffixed variables) and
lead_time + lead_subtime. Select
variables, the point and exact hours BEFORE loading any forecast values.
"""
from __future__ import annotations

from feature_flags import require_weathernext3

import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from gfs_core import GfsRun, ProfileResult
from weathernext3_math import dewpoint_from_specific_humidity, relative_humidity, wind_from
from weathernext3_provider import WeatherNext3Error, _utc, validate_coordinates
from weathernext3_cache import cache_key, cached_json
from weathernext3_cancel import check_cancelled

LEVELS = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50)
VARIABLES = ('temperature', 'specific_humidity', 'geopotential', 'u_component_of_wind', 'v_component_of_wind')
BUCKET = 'weathernext3_spatial'
PREFIX = 'weathernext_3_0_0/zarr/2026_to_present/'
RUN_RE = re.compile(r'(\d{8})_(\d{2})hr_(\d+)_preds/')


def ensure_upper_runtime() -> None:
    if sys.version_info < (3, 11):
        raise WeatherNext3Error('Для вертикальной WeatherNext 3 через GCS/Zarr нужен Python 3.11+. '
                                'На Python 3.10 доступны GFS и поверхностная WN3 через BigQuery.')


@dataclass(frozen=True)
class ZarrRun:
    init: datetime
    uri: str

    @property
    def revision(self):
        match = RUN_RE.search(self.uri)
        return int(match[3]) if match else 0


def _hours(values) -> np.ndarray:
    values = np.asarray(values)
    if not np.issubdtype(values.dtype, np.timedelta64):
        raise WeatherNext3Error('WN3 Zarr: lead coordinates должны иметь тип timedelta')
    floats = values / np.timedelta64(1, 'h')
    if not np.all(np.isfinite(floats)) or not np.all(floats == np.round(floats)):
        raise WeatherNext3Error('WN3 Zarr: некорректные часовые координаты')
    return floats.astype(int)


def select_hours(array, leads: Sequence[int]):
    """Vectorized paired indexing avoids loading an outer time×subtime product."""
    import xarray as xr
    outer = _hours(array['lead_time'].values).reshape(-1)
    offsets = _hours(array['lead_subtime'].values).reshape(-1) if 'lead_subtime' in array.dims else np.array([0])
    positions: dict[int, tuple[int, int]] = {}
    for i, base in enumerate(outer):
        for j, offset in enumerate(offsets):
            hour = int(base + offset)
            if hour in positions:
                raise WeatherNext3Error(f'WN3 Zarr: дублированный срок +{hour}')
            positions[hour] = (i, j)
    missing = set(leads) - positions.keys()
    if missing:
        raise WeatherNext3Error('WN3 Zarr: отсутствуют сроки ' + ', '.join(map(str, sorted(missing))))
    indexers = {'lead_time': xr.DataArray([positions[h][0] for h in leads], dims='forecast_hour')}
    if 'lead_subtime' in array.dims:
        indexers['lead_subtime'] = xr.DataArray([positions[h][1] for h in leads], dims='forecast_hour')
    return array.isel(indexers).assign_coords(forecast_hour=list(leads))


def select_point(array, lat: float, lon: float):
    latitude = next((name for name in array.dims if name.startswith('lat')), None)
    longitude = next((name for name in array.dims if name.startswith('lon')), None)
    if latitude is None or longitude is None:
        raise WeatherNext3Error('WN3 Zarr: нет широты/долготы в поле')
    # Circular nearest neighbour also works at the 0/360 seam and for descending grids.
    lats = np.asarray(array[latitude].values, dtype=float)
    lons = np.asarray(array[longitude].values, dtype=float)
    iy = int(np.argmin(np.abs(lats - lat)))
    ix = int(np.argmin(np.abs((lons - lon + 180) % 360 - 180)))
    if abs(lats[iy] - lat) > 0.251 or abs((lons[ix] - lon + 180) % 360 - 180) > 0.251:
        raise WeatherNext3Error('WN3 Zarr не покрывает запрошенную точку')
    return array.isel({latitude: iy, longitude: ix}), float(lats[iy]), float((lons[ix] + 180) % 360 - 180)


class WeatherNext3ZarrProvider:
    def __init__(self, *, billing_project: str = '', candidates: Callable[[], Sequence[ZarrRun]] | None = None,
                 opener: Callable[[str], Any] | None = None, max_subset_bytes: int = 32 * 1024 * 1024,
                 max_chunk_bytes: int = 128 * 1024 * 1024, max_read_bytes: int = 512 * 1024 * 1024, cache_dir=None):
        self.billing_project = billing_project
        self._candidates = candidates
        self._opener = opener
        self.max_subset_bytes = int(max_subset_bytes)
        self.max_chunk_bytes = int(max_chunk_bytes)
        self.max_read_bytes = int(max_read_bytes)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if min(self.max_subset_bytes, self.max_chunk_bytes, self.max_read_bytes) <= 0:
            raise WeatherNext3Error('Лимиты подвыборки/чанка WN3 должны быть положительными')

    @classmethod
    def from_env(cls):
        require_weathernext3()
        ensure_upper_runtime()
        return cls(billing_project=os.getenv('WEATHERNEXT3_GCS_BILLING_PROJECT', ''),
                   max_subset_bytes=int(os.getenv('WEATHERNEXT3_ZARR_MAX_SUBSET_BYTES', '33554432')),
                   max_chunk_bytes=int(os.getenv('WEATHERNEXT3_ZARR_MAX_CHUNK_BYTES', '134217728')),
                   max_read_bytes=int(os.getenv('WEATHERNEXT3_ZARR_MAX_READ_BYTES', '536870912')),
                   cache_dir=Path(os.getenv('GFS_CACHE_DIR', '.cache_gfs')) / 'weathernext3' / 'zarr')

    def runs(self) -> list[ZarrRun]:
        require_weathernext3()
        check_cancelled()
        if self.cache_dir is None or self._candidates is not None:
            return self._list_runs()
        def build():
            return [{'init': run.init.isoformat(), 'uri': run.uri} for run in self._list_runs()]
        def validate(rows):
            if not isinstance(rows, list) or not rows:
                raise WeatherNext3Error('Каталог WN3 пуст')
            for row in rows:
                _utc(row['init'])
                if not row['uri'].startswith(f'gs://{BUCKET}/{PREFIX}'):
                    raise WeatherNext3Error('Некорректный каталог в кэше WN3')
        rows = cached_json(self.cache_dir / ('catalog_' + cache_key(self.billing_project) + '.json'), 300, build, validate)
        return [ZarrRun(_utc(row['init']), row['uri']) for row in rows]

    def _list_runs(self) -> list[ZarrRun]:
        require_weathernext3()
        if self._candidates is not None:
            return sorted(self._candidates(), key=lambda run: (run.init, run.revision, run.uri), reverse=True)
        ensure_upper_runtime()
        if not self.billing_project:
            raise WeatherNext3Error('Для вертикальной WN3 задайте WEATHERNEXT3_GCS_BILLING_PROJECT и ADC; GCS использует Requester Pays')
        try:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/devstorage.read_only'])
            found = []
            with AuthorizedSession(credentials) as session:
                for days in range(4):
                    check_cancelled()
                    day = datetime.now(timezone.utc) - timedelta(days=days)
                    params = {'prefix': PREFIX + day.strftime('%Y%m%d'), 'delimiter': '/',
                              'maxResults': 1000, 'userProject': self.billing_project, 'fields': 'prefixes,nextPageToken'}
                    for page in range(10):
                        check_cancelled()
                        response = session.get(f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/o', params=params, timeout=30)
                        response.raise_for_status()
                        payload = response.json()
                        for prefix in payload.get('prefixes', []):
                            match = RUN_RE.search(prefix)
                            if match and int(match[2]) in (0, 6, 12, 18):
                                init = datetime.strptime(match[1] + match[2], '%Y%m%d%H').replace(tzinfo=timezone.utc)
                                found.append(ZarrRun(init, f'gs://{BUCKET}/{prefix}predictions.zarr'))
                        if not payload.get('nextPageToken'):
                            break
                        params['pageToken'] = payload['nextPageToken']
                    else:
                        raise WeatherNext3Error('Превышен лимит страниц каталога WN3')
            return sorted(found, key=lambda run: (run.init, run.revision, run.uri), reverse=True)
        except WeatherNext3Error:
            raise
        except Exception as exc:
            raise WeatherNext3Error(f'Каталог WN3 GCS недоступен ({type(exc).__name__}); проверьте ADC/allowlist/billing') from exc

    def open(self, uri: str):
        require_weathernext3()
        if self._opener:
            return self._opener(uri)
        ensure_upper_runtime()
        try:
            import obstore.store
            import xarray as xr
            import zarr
            if not uri.startswith(f'gs://{BUCKET}/{PREFIX}'):
                raise WeatherNext3Error('Недопустимый каталог WN3 GCS')
            store = obstore.store.GCSStore(bucket=BUCKET, prefix=uri.split('/', 3)[3],
                client_options={'default_headers': {'x-goog-user-project': self.billing_project}})
            return xr.open_zarr(zarr.storage.ObjectStore(store, read_only=True), chunks=None, consolidated=False)
        except ImportError as exc:
            raise WeatherNext3Error('Для GCS/Zarr используйте Python 3.11+ и requirements-weathernext3.txt') from exc

    def profiles(self, lat: float, lon: float, leads: Sequence[int], *, member: str = 'mean', progress=None) -> list[ProfileResult]:
        require_weathernext3()
        validate_coordinates(lat, lon)
        leads = sorted(set(int(h) for h in leads))
        if not leads or min(leads) < 1 or max(leads) > 360 or len(leads) > 32:
            raise WeatherNext3Error('WN3: 1..360 ч, не более 32 профилей за запрос')
        if member != 'mean' and (not str(member).isdigit() or not 0 <= int(member) < 64):
            raise WeatherNext3Error('member: mean или 0..63')
        last_error = None
        for run in self.runs()[:16]:
            if run.init.hour not in (0, 6, 12, 18):
                continue
            dataset = None
            try:
                check_cancelled()
                if progress:
                    progress(f'Проверяю WN3 GCS {run.init:%d.%m %HZ}')
                dataset = self.open(run.uri)
                init_values = np.asarray(dataset['init_time'].values).reshape(-1)
                if init_values.size != 1 or _utc(pd.Timestamp(init_values[0]).to_pydatetime()) != run.init:
                    raise WeatherNext3Error('WN3 Zarr: init_time не совпадает с каталогом')
                results = self._profiles_from_dataset(dataset, run, lat, lon, leads, member, progress)
                return results
            except (WeatherNext3Error, KeyError, ValueError) as exc:
                last_error = exc
            finally:
                if dataset is not None:
                    dataset.close()
        raise WeatherNext3Error(f'Нет полного опубликованного WN3-профиля: {last_error or "каталог пуст"}')

    def _profiles_from_dataset(self, dataset, run, lat, lon, leads, member, progress=None):
        arrays = {}
        grid = None
        samples = None
        subset_bytes = 0
        reads = {}
        for level in LEVELS:
            for name in VARIABLES:
                key = f'{name}_{level}'
                source_key = key if key in dataset else name
                if source_key not in dataset:
                    raise WeatherNext3Error(f'WN3 Zarr: отсутствует {key}')
                original = dataset[source_key]
                chunks = original.encoding.get('chunks')
                if chunks and math.prod(chunks) * original.dtype.itemsize > self.max_chunk_bytes:
                    raise WeatherNext3Error('Чанк WN3 превышает WEATHERNEXT3_ZARR_MAX_CHUNK_BYTES')
                array = original
                if 'level' in array.dims:
                    array = array.sel(level=level)  # raw, unflattened pressure-coordinate form
                elif source_key == name:
                    raise WeatherNext3Error(f'WN3 Zarr: в {name} нет координаты level')
                array, grid_lat, grid_lon = select_point(array, lat, lon)
                if grid is not None and grid != (grid_lat, grid_lon):
                    raise WeatherNext3Error('WN3: атмосферные поля имеют разные узлы')
                grid = (grid_lat, grid_lon)
                if 'init_time' in array.dims:
                    array = array.isel(init_time=0)
                array = select_hours(array, leads)
                if 'sample' not in array.dims:
                    raise WeatherNext3Error('WN3 raw ensemble: отсутствует sample')
                current_samples = np.asarray(array['sample'].values)
                if len(current_samples) != len(set(current_samples.tolist())):
                    raise WeatherNext3Error('WN3: дублированные члены ансамбля')
                if samples is not None and not np.array_equal(samples, current_samples):
                    raise WeatherNext3Error('WN3: sample координаты полей не совпадают')
                samples = current_samples
                if member != 'mean':
                    array = array.sel(sample=[int(member)])
                if set(array.dims) != {'sample', 'forecast_hour'}:
                    raise WeatherNext3Error(f'Неожиданные размерности WN3: {array.dims}')
                subset_bytes += array.size * array.dtype.itemsize
                if subset_bytes > self.max_subset_bytes:
                    raise WeatherNext3Error('Подвыборка WN3 превышает лимит памяти')
                arrays[key] = array.transpose('sample', 'forecast_hour')
                # Conservative uncompressed chunk-read bound, separate from selected-array RAM.
                if chunks:
                    entry = reads.setdefault(source_key, {'chunk_bytes': math.prod(chunks)*original.dtype.itemsize,
                                                          'bins': {dim: set() for dim in original.dims}})
                    for dim, width in zip(original.dims, chunks):
                        chosen = np.asarray(array[dim].values).reshape(-1)
                        full = np.asarray(original[dim].values).reshape(-1)
                        for coordinate in chosen:
                            indices = np.flatnonzero(full == coordinate)
                            if indices.size != 1:
                                raise WeatherNext3Error(f'Неоднозначная координата WN3 {dim}')
                            entry['bins'][dim].add(int(indices[0]) // int(width))
                check_cancelled()
        def materialize():
            estimate = sum(entry['chunk_bytes'] * math.prod(len(bins) for bins in entry['bins'].values()) for entry in reads.values())
            if estimate > self.max_read_bytes:
                raise WeatherNext3Error(f'Оценка чтения чанков WN3 {estimate} байт превышает WEATHERNEXT3_ZARR_MAX_READ_BYTES; уменьшите число сроков/членов')
            # Only bounded point×sample×lead arrays are materialized here.
            values = {}
            for key, array in arrays.items():
                check_cancelled()
                if progress:
                    progress(f'Читаю WN3: {key}')
                values[key] = np.asarray(array.values, dtype=float)
            rows = {lead: [] for lead in leads}
            for level in LEVELS:
                t = values[f'temperature_{level}'] - 273.15
                q = values[f'specific_humidity_{level}']
                td = dewpoint_from_specific_humidity(q, level)
                u, v = values[f'u_component_of_wind_{level}'], values[f'v_component_of_wind_{level}']
                z = values[f'geopotential_{level}'] / 9.80665
                if not all(np.isfinite(a).all() for a in (t, td, u, v, z)):
                    raise WeatherNext3Error('WN3: неполные/повреждённые атмосферные значения')
                tm, tdm, um, vm, zm = (np.mean(a, axis=0) for a in (t, td, u, v, z))
                # Keep mean scalar speed, not magnitude of the mean wind vector.
                speed = np.mean(np.hypot(u, v), axis=0)
                direction = wind_from(um, vm)
                rh = np.mean(relative_humidity(t, td), axis=0)
                for index, lead in enumerate(leads):
                    rows[lead].append({'pressure_hpa': level, 'geopotential_height_m': zm[index],
                        'geopotential_height_km': zm[index]/1000, 'temperature_c': tm[index], 'temperature_k': tm[index]+273.15,
                        'dewpoint_c': tdm[index], 'relative_humidity_pct': rh[index],
                        'u_wind_ms': um[index], 'v_wind_ms': vm[index],
                        'wind_speed_ms': float(speed[index]), 'wind_dir_deg': float(direction[index]) if np.isfinite(direction[index]) else None})
            return {str(lead): records for lead, records in rows.items()}
        def validate_rows(value):
            if not isinstance(value, dict) or set(value) != {str(lead) for lead in leads}:
                raise WeatherNext3Error('Повреждён кэш сроков WN3')
            for records in value.values():
                if [row.get('pressure_hpa') for row in records] != list(LEVELS):
                    raise WeatherNext3Error('Повреждён кэш уровней WN3')
                for row in records:
                    for key in ('temperature_c', 'dewpoint_c', 'geopotential_height_m', 'u_wind_ms', 'v_wind_ms', 'wind_speed_ms'):
                        if not math.isfinite(float(row[key])):
                            raise WeatherNext3Error('Повреждены значения кэша WN3')
        if self.cache_dir is not None:
            signature = {'schema': 2, 'uri': run.uri, 'grid': grid, 'levels': LEVELS, 'leads': leads, 'member': member}
            rows = cached_json(self.cache_dir / ('profile_' + cache_key(signature) + '.json'), 86400, materialize, validate_rows)
        else:
            rows = materialize()
            validate_rows(rows)
        check_cancelled()
        count = len(samples) if member == 'mean' else 1
        label = 'WeatherNext 3 0.25° · ' + (f'средний профиль ({count}/64)' if member == 'mean' else f'член {member}')
        return [ProfileResult(GfsRun(run.init.strftime('%Y%m%d'), run.init.strftime('%H')), lead,
                    lat, lon, *grid, Path('wn3-zarr'), pd.DataFrame(rows[str(lead)]),
                    model_label=label, source_label='Google GCS/Zarr',
                    diagnostics_note='13 уровней; уровни ниже рельефа не маскированы. Диагностика среднего профиля — не среднее диагностики ансамбля.' if member == 'mean' else '13 уровней; отдельный член; уровни ниже рельефа не маскированы.')
                for lead in leads]
