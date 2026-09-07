from __future__ import annotations

"""WeatherNext 3 surface-statistics provider backed by Google BigQuery."""

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class WeatherNext3Error(RuntimeError):
    pass


class QueryExecutor(Protocol):
    def query(self, sql: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class WeatherNext3Run:
    init_time_utc: datetime
    horizon_hours: int

    @property
    def is_synoptic(self) -> bool:
        return self.init_time_utc.hour in {0, 6, 12, 18}

    @property
    def label(self) -> str:
        kind = "основной" if self.is_synoptic else "промежуточный"
        return f"{self.init_time_utc:%Y-%m-%d %HZ} · {kind} · до +{self.horizon_hours} ч"


@dataclass(slots=True)
class WeatherNext3PointSeries:
    run: WeatherNext3Run
    point_label: str
    requested_lat: float
    requested_lon: float
    grid_lat: float | None
    grid_lon: float | None
    station_grid_lat: float | None
    station_grid_lon: float | None
    rows: list[dict[str, Any]]


@dataclass(slots=True)
class WeatherNext3MapFrame:
    run: WeatherNext3Run
    lead_hour: int
    valid_time_utc: datetime
    point_label: str
    requested_lat: float
    requested_lon: float
    radius_km: float
    kind: str
    rows: list[dict[str, Any]]


_TABLE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_STAT_SUFFIXES = ("mean", "p10", "p25", "p50", "p75", "p90")
MAP_KINDS = ("clouds", "cloud_layers", "precip_native", "precip_imerg", "precip_experimental", "combo")


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _clean_identifier(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not value or not _TABLE_RE.fullmatch(value):
        raise WeatherNext3Error(f"Некорректный {name}: {value!r}")
    return value


class GoogleBigQueryExecutor:
    def __init__(self, *, billing_project: str, location: str | None = None, maximum_bytes_billed: int | None = None, timeout_seconds: float = 180.0, client: Any | None = None) -> None:
        self.billing_project = _clean_identifier(billing_project, "billing project")
        self.location = str(location).strip() if location else None
        self.maximum_bytes_billed = int(maximum_bytes_billed or 0) or None
        self.timeout_seconds = max(10.0, float(timeout_seconds))
        self._client = client

    @classmethod
    def from_env(cls, *, default_project: str) -> "GoogleBigQueryExecutor":
        raw_limit = _env("WEATHERNEXT3_BQ_MAX_BYTES_BILLED", "WN3_BQ_MAX_BYTES_BILLED", default="0")
        try:
            maximum = int(raw_limit or "0")
        except ValueError as exc:
            raise WeatherNext3Error("WEATHERNEXT3_BQ_MAX_BYTES_BILLED должен быть целым числом байт") from exc
        return cls(
            billing_project=_env("WEATHERNEXT3_BIGQUERY_BILLING_PROJECT", "WN3_BIGQUERY_BILLING_PROJECT", default=default_project),
            location=_env("WEATHERNEXT3_BIGQUERY_LOCATION", "WN3_BIGQUERY_LOCATION") or None,
            maximum_bytes_billed=maximum or None,
            timeout_seconds=float(_env("WEATHERNEXT3_BIGQUERY_TIMEOUT", "WN3_BIGQUERY_TIMEOUT", default="180")),
        )

    def _google_client(self):
        if self._client is not None:
            return self._client
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise WeatherNext3Error("Для WeatherNext 3 установите google-cloud-bigquery и настройте Application Default Credentials") from exc
        self._client = bigquery.Client(project=self.billing_project)
        return self._client

    @staticmethod
    def _parameter(name: str, value: Any):
        from google.cloud import bigquery
        if isinstance(value, (list, tuple)):
            first = value[0] if value else 0
            scalar_type = "INT64" if isinstance(first, int) and not isinstance(first, bool) else "STRING"
            return bigquery.ArrayQueryParameter(name, scalar_type, list(value))
        if isinstance(value, datetime):
            return bigquery.ScalarQueryParameter(name, "TIMESTAMP", value)
        if isinstance(value, bool):
            return bigquery.ScalarQueryParameter(name, "BOOL", value)
        if isinstance(value, int):
            return bigquery.ScalarQueryParameter(name, "INT64", value)
        if isinstance(value, float):
            return bigquery.ScalarQueryParameter(name, "FLOAT64", value)
        return bigquery.ScalarQueryParameter(name, "STRING", str(value))

    def query(self, sql: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise WeatherNext3Error("Для WeatherNext 3 установите google-cloud-bigquery и настройте Application Default Credentials") from exc
        job_config = bigquery.QueryJobConfig(query_parameters=[self._parameter(name, value) for name, value in parameters.items()])
        if self.maximum_bytes_billed:
            job_config.maximum_bytes_billed = self.maximum_bytes_billed
        try:
            job = self._google_client().query(sql, job_config=job_config, location=self.location)
            return [dict(row.items()) for row in job.result(timeout=self.timeout_seconds)]
        except Exception as exc:
            if "maximum bytes billed" in str(exc).lower():
                raise WeatherNext3Error("Запрос WeatherNext 3 превышает WEATHERNEXT3_BQ_MAX_BYTES_BILLED; уменьшите область/период или скорректируйте лимит") from exc
            raise WeatherNext3Error(f"BigQuery WeatherNext 3 недоступен: {exc}") from exc


class WeatherNext3Provider:
    TABLE_01 = "weathernext_3_0_0_0p1deg"
    TABLE_005 = "weathernext_3_0_0_0p05deg"

    def __init__(self, *, project: str, dataset: str, executor: QueryExecutor, cache_dir: str | Path | None = None, cache_ttl_seconds: int = 1800) -> None:
        self.project = _clean_identifier(project, "WeatherNext 3 project")
        self.dataset = _clean_identifier(dataset, "WeatherNext 3 dataset")
        self.executor = executor
        self.cache_dir = Path(cache_dir or (Path(os.getenv("GFS_CACHE_DIR", ".cache_gfs")) / "weathernext3"))
        self.cache_ttl_seconds = max(60, int(cache_ttl_seconds))

    @classmethod
    def from_env(cls, *, executor: QueryExecutor | None = None) -> "WeatherNext3Provider":
        project = _env("WEATHERNEXT3_BIGQUERY_PROJECT", "WN3_BIGQUERY_PROJECT")
        dataset = _env("WEATHERNEXT3_BIGQUERY_DATASET", "WN3_BIGQUERY_DATASET")
        if not project or not dataset:
            raise WeatherNext3Error("WeatherNext 3 не настроен: задайте WEATHERNEXT3_BIGQUERY_PROJECT и WEATHERNEXT3_BIGQUERY_DATASET после подписки на WeatherNext 3 в BigQuery Analytics Hub")
        if executor is None:
            executor = GoogleBigQueryExecutor.from_env(default_project=project)
        ttl = int(_env("WEATHERNEXT3_CACHE_TTL", "WN3_CACHE_TTL", default="1800"))
        return cls(project=project, dataset=dataset, executor=executor, cache_ttl_seconds=ttl)

    @property
    def table_01(self) -> str:
        return f"`{self.project}.{self.dataset}.{self.TABLE_01}`"

    @property
    def table_005(self) -> str:
        return f"`{self.project}.{self.dataset}.{self.TABLE_005}`"

    def _cache_path(self, namespace: str, payload: Mapping[str, Any]) -> Path:
        packed = json.dumps({"project": self.project, "dataset": self.dataset, "namespace": namespace, "payload": payload}, sort_keys=True, default=_json_value).encode()
        return self.cache_dir / f"{namespace}_{hashlib.sha256(packed).hexdigest()[:28]}.json"

    def _query_cached(self, namespace: str, sql: str, parameters: Mapping[str, Any], *, ttl_seconds: int | None = None) -> list[dict[str, Any]]:
        path = self._cache_path(namespace, parameters)
        ttl = self.cache_ttl_seconds if ttl_seconds is None else max(1, int(ttl_seconds))
        try:
            if time.time() - path.stat().st_mtime <= ttl:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    return [dict(item) for item in payload if isinstance(item, dict)]
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
            path.unlink(missing_ok=True)
        rows = self.executor.query(sql, parameters)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps([{str(k): _json_value(v) for k, v in row.items()} for row in rows], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
        return rows

    def latest_run(self, lat: float, lon: float, required_hour: int = 48) -> WeatherNext3Run:
        required_hour = max(1, int(required_hour))
        if required_hour > 360:
            raise WeatherNext3Error("WeatherNext 3 доступен до +360 ч")
        sql = f"""
        SELECT t.init_time, MAX(f.hours) AS max_hour
        FROM {self.table_01} AS t
        CROSS JOIN UNNEST(t.forecast) AS f
        WHERE t.init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 72 HOUR)
          AND ST_INTERSECTS(t.geography_polygon, ST_GEOGPOINT(@lon, @lat))
        GROUP BY t.init_time
        HAVING MAX(f.hours) >= @required_hour
        ORDER BY t.init_time DESC
        LIMIT 1
        """
        rows = self._query_cached("latest_run", sql, {"lat": float(lat), "lon": float(lon), "required_hour": required_hour}, ttl_seconds=min(self.cache_ttl_seconds, 600))
        if not rows:
            raise WeatherNext3Error(f"Нет опубликованного WeatherNext 3 для точки со сроком +{required_hour} ч")
        row = rows[0]
        return WeatherNext3Run(_utc(row["init_time"]), int(row.get("max_hour") or required_hour))

    @staticmethod
    def _surface_select() -> list[str]:
        fields = [
            "f.time AS forecast_time", "f.hours AS forecast_hour",
            "ST_X(t.geography) AS grid_lon", "ST_Y(t.geography) AS grid_lat",
            "f.mean_sea_level_pressure_mean AS pressure_mean",
            "f.total_cloud_cover_mean AS cloud_total_mean", "f.total_cloud_cover_p10 AS cloud_total_p10", "f.total_cloud_cover_p90 AS cloud_total_p90",
            "f.low_cloud_cover_mean AS cloud_low_mean", "f.medium_cloud_cover_mean AS cloud_mid_mean", "f.high_cloud_cover_mean AS cloud_high_mean",
            "f.u_component_of_wind_10m_mean AS wind_u_mean", "f.v_component_of_wind_10m_mean AS wind_v_mean",
            "f.imerg_tp_1hr_mean AS precip_imerg_mean", "f.experimental_tp_1hr_mean AS precip_experimental_mean",
            "f.surface_solar_radiation_downwards_1hr_mean AS solar_mean",
        ]
        for suffix in _STAT_SUFFIXES:
            fields += [
                f"f.temperature_2m_{suffix} AS temperature_{suffix}",
                f"f.dewpoint_temperature_2m_{suffix} AS dewpoint_{suffix}",
                f"f.wind_speed_10m_{suffix} AS wind_speed_{suffix}",
                f"f.total_precipitation_1hr_{suffix} AS precip_native_{suffix}",
            ]
        return fields

    @staticmethod
    def _station_select() -> list[str]:
        fields = ["f.time AS forecast_time", "f.hours AS forecast_hour", "ST_X(t.geography) AS station_grid_lon", "ST_Y(t.geography) AS station_grid_lat"]
        for suffix in _STAT_SUFFIXES:
            fields += [
                f"f.station_head_temperature_2m_{suffix} AS station_temperature_{suffix}",
                f"f.station_head_dewpoint_temperature_2m_{suffix} AS station_dewpoint_{suffix}",
            ]
        return fields

    def point_series(self, point_label: str, lat: float, lon: float, lead_to: int, *, run: WeatherNext3Run | None = None) -> WeatherNext3PointSeries:
        lead_to = max(1, int(lead_to))
        selected = run or self.latest_run(lat, lon, lead_to)
        params = {"lat": float(lat), "lon": float(lon), "lead_to": lead_to, "init_time": selected.init_time_utc}
        surface_sql = f"""
        SELECT {', '.join(self._surface_select())}
        FROM {self.table_01} AS t
        CROSS JOIN UNNEST(t.forecast) AS f
        WHERE t.init_time = @init_time
          AND ST_INTERSECTS(t.geography_polygon, ST_GEOGPOINT(@lon, @lat))
          AND f.hours BETWEEN 1 AND @lead_to
        ORDER BY f.hours
        """
        station_sql = f"""
        SELECT {', '.join(self._station_select())}
        FROM {self.table_005} AS t
        CROSS JOIN UNNEST(t.forecast) AS f
        WHERE t.init_time = @init_time
          AND ST_INTERSECTS(t.geography_polygon, ST_GEOGPOINT(@lon, @lat))
          AND f.hours BETWEEN 1 AND @lead_to
        ORDER BY f.hours
        """
        surface = self._query_cached("point_surface", surface_sql, params)
        if not surface:
            raise WeatherNext3Error("WeatherNext 3 не вернул поверхностный ряд для выбранной точки")
        try:
            station = self._query_cached("point_station", station_sql, params)
        except WeatherNext3Error:
            station = []
        by_hour = {int(row["forecast_hour"]): dict(row) for row in surface}
        station_by_hour = {int(row["forecast_hour"]): dict(row) for row in station}
        for hour, row in by_hour.items():
            station_row = station_by_hour.get(hour)
            if station_row:
                row.update({key: value for key, value in station_row.items() if key.startswith("station_")})
        rows = [by_hour[key] for key in sorted(by_hour)]
        first = rows[0]
        station_first = station_by_hour.get(int(first["forecast_hour"]), {})
        return WeatherNext3PointSeries(selected, str(point_label), float(lat), float(lon), _float_or_none(first.get("grid_lat")), _float_or_none(first.get("grid_lon")), _float_or_none(station_first.get("station_grid_lat")), _float_or_none(station_first.get("station_grid_lon")), rows)

    @staticmethod
    def _map_columns(kind: str) -> list[str]:
        if kind == "clouds":
            return ["f.total_cloud_cover_mean AS cloud_total"]
        if kind == "cloud_layers":
            return ["f.low_cloud_cover_mean AS cloud_low", "f.medium_cloud_cover_mean AS cloud_mid", "f.high_cloud_cover_mean AS cloud_high", "f.total_cloud_cover_mean AS cloud_total"]
        if kind == "precip_native":
            return ["f.total_precipitation_1hr_mean AS precip"]
        if kind == "precip_imerg":
            return ["f.imerg_tp_1hr_mean AS precip"]
        if kind == "precip_experimental":
            return ["f.experimental_tp_1hr_mean AS precip"]
        if kind == "combo":
            return ["f.total_cloud_cover_mean AS cloud_total", "f.total_precipitation_1hr_mean AS precip"]
        raise WeatherNext3Error(f"Неизвестный слой WeatherNext 3: {kind}")

    def map_frames(self, point_label: str, lat: float, lon: float, leads: Sequence[int], *, radius_km: float = 150.0, kind: str = "combo", run: WeatherNext3Run | None = None) -> list[WeatherNext3MapFrame]:
        kind = str(kind).lower()
        if kind not in MAP_KINDS:
            raise WeatherNext3Error(f"Неизвестный слой WeatherNext 3: {kind}")
        lead_values = sorted({int(value) for value in leads})
        if not lead_values or lead_values[0] < 1 or lead_values[-1] > 360:
            raise WeatherNext3Error("Сроки WeatherNext 3 должны быть в диапазоне 1..360 ч")
        if len(lead_values) > 32:
            raise WeatherNext3Error("В одной анимации WeatherNext 3 допускается не более 32 кадров")
        radius_km = float(radius_km)
        if not 25 <= radius_km <= 500:
            raise WeatherNext3Error("Радиус карты WeatherNext 3 должен быть 25..500 км")
        selected = run or self.latest_run(lat, lon, max(lead_values))
        columns = ["f.time AS forecast_time", "f.hours AS forecast_hour", "ST_X(t.geography) AS longitude", "ST_Y(t.geography) AS latitude", *self._map_columns(kind)]
        sql = f"""
        SELECT {', '.join(columns)}
        FROM {self.table_01} AS t
        CROSS JOIN UNNEST(t.forecast) AS f
        WHERE t.init_time = @init_time
          AND f.hours IN UNNEST(@leads)
          AND ST_DWITHIN(t.geography, ST_GEOGPOINT(@lon, @lat), @radius_m)
        ORDER BY f.hours, latitude, longitude
        """
        params = {"init_time": selected.init_time_utc, "leads": lead_values, "lat": float(lat), "lon": float(lon), "radius_m": radius_km * 1000.0}
        rows = self._query_cached(f"map_{kind}", sql, params)
        groups: dict[int, list[dict[str, Any]]] = {value: [] for value in lead_values}
        valid_times: dict[int, datetime] = {}
        for raw in rows:
            row = dict(raw)
            hour = int(row["forecast_hour"])
            for key in ("cloud_total", "cloud_low", "cloud_mid", "cloud_high"):
                if row.get(key) is not None:
                    row[key] = float(row[key]) * 100.0
            if row.get("precip") is not None:
                row["precip"] = float(row["precip"]) * 1000.0
            groups.setdefault(hour, []).append(row)
            valid_times[hour] = _utc(row["forecast_time"])
        missing = [hour for hour in lead_values if not groups.get(hour)]
        if missing:
            raise WeatherNext3Error("WeatherNext 3 не вернул кадры: " + ", ".join(f"+{value}" for value in missing))
        return [WeatherNext3MapFrame(selected, hour, valid_times[hour], str(point_label), float(lat), float(lon), radius_km, kind, groups[hour]) for hour in lead_values]


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def provider_from_env() -> WeatherNext3Provider:
    return WeatherNext3Provider.from_env()
