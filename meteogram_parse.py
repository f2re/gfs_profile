from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from meteogram_models import MeteogramError, MeteogramSeries, MeteogramSource

DETERMINISTIC_PARAMETERS = (
    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "precipitation", "weather_code", "pressure_msl", "cloud_cover",
    "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "is_day",
)
ENSEMBLE_PARAMETERS = (
    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "precipitation", "weather_code", "pressure_msl", "cloud_cover",
    "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
)
MEMBER_RE = re.compile(r"^(?P<name>.+)_member(?P<member>\d+)$")

EXPECTED_UNITS = {
    "temperature_2m": {"°C", "C", "celsius"},
    "dew_point_2m": {"°C", "C", "celsius"},
    "relative_humidity_2m": {"%"},
    "precipitation": {"mm"},
    "pressure_msl": {"hPa", "гПа"},
    "wind_speed_10m": {"m/s", "ms"},
    "wind_gusts_10m": {"m/s", "ms"},
    "wind_direction_10m": {"°", "degree", "degrees"},
}

def parse_deterministic_payload(
    payload: dict[str, Any], *, source: MeteogramSource, point_label: str,
    requested_lat: float, requested_lon: float,
) -> MeteogramSeries:
    meta = _metadata(payload, source, point_label, requested_lat, requested_lon)
    hourly = payload.get("hourly") or {}
    count = len(meta["times"])
    fields = {name: _numeric(hourly.get(name), count) for name in DETERMINISTIC_PARAMETERS}
    fields["precipitation_intensity"] = _precip_intensity(fields["precipitation"], meta["times"])
    _require_finite_field(fields, "temperature_2m", source)
    return MeteogramSeries(fields=fields, **meta)


def parse_ensemble_payload(
    payload: dict[str, Any], *, source: MeteogramSource, point_label: str,
    requested_lat: float, requested_lon: float,
) -> MeteogramSeries:
    meta = _metadata(payload, source, point_label, requested_lat, requested_lon)
    hourly = payload.get("hourly") or {}
    count = len(meta["times"])
    members: dict[str, dict[int, np.ndarray]] = {}
    for key, values in hourly.items():
        match = MEMBER_RE.match(str(key))
        if match:
            members.setdefault(match.group("name"), {})[int(match.group("member"))] = _numeric(values, count)
    if not members:
        raise MeteogramError("Ансамблевый ответ не содержит отдельных членов")
    all_ids = {member for group in members.values() for member in group}
    fields: dict[str, np.ndarray] = {}
    stats: dict[str, dict[str, np.ndarray]] = {}
    coverage_counts: list[np.ndarray] = []
    coverage_parameters = {
        "temperature_2m",
        "precipitation",
        "wind_speed_10m",
    }
    for name in ENSEMBLE_PARAMETERS:
        matrix = _matrix(members.get(name, {}), count)
        if matrix.size == 0:
            fields[name] = np.full(count, np.nan)
            continue
        if name in coverage_parameters:
            coverage_counts.append(np.sum(np.isfinite(matrix), axis=0))
        if name == "wind_direction_10m":
            fields[name], fields["wind_direction_resultant"] = _circular_mean(matrix)
            continue
        if name == "weather_code":
            fields[name] = _column_mode(matrix)
            continue
        q10, q25, q50, q75, q90 = (_nanquantile(matrix, q) for q in (0.10, 0.25, 0.50, 0.75, 0.90))
        mean = np.nanmean(matrix, axis=0)
        fields[name] = mean if name in {"temperature_2m", "dew_point_2m", "pressure_msl"} else q50
        stats[name] = {"q10": q10, "q25": q25, "q50": q50, "q75": q75, "q90": q90, "mean": mean}
        if name == "precipitation":
            # Daily ensemble totals must be calculated member-by-member.
            # Summing pointwise medians is not the median of accumulated totals.
            stats[name]["members"] = matrix.copy()
            valid = np.sum(np.isfinite(matrix), axis=0)
            for threshold, suffix in ((0.1, "0p1"), (1.0, "1"), (5.0, "5")):
                events = np.sum(np.where(np.isfinite(matrix), matrix >= threshold, False), axis=0)
                fields[f"precipitation_probability_{suffix}"] = np.divide(events * 100.0, valid, out=np.full(count, np.nan), where=valid > 0)
    fields["precipitation_intensity"] = _precip_intensity(fields.get("precipitation", np.full(count, np.nan)), meta["times"])
    if "precipitation" in stats:
        for stat in ("q10", "q25", "q50", "q75", "q90"):
            stats["precipitation"][f"{stat}_intensity"] = _precip_intensity(stats["precipitation"][stat], meta["times"])
    populated_groups = [
        len(members[name])
        for name in coverage_parameters
        if members.get(name)
    ]
    observed = min(populated_groups) if populated_groups else len(all_ids)
    expected = source.expected_members or observed
    warnings = list(meta.pop("warnings"))
    if coverage_counts:
        per_time_count = np.min(np.vstack(coverage_counts), axis=0).astype(float)
        fields["ensemble_member_count"] = per_time_count
        fields["ensemble_member_coverage"] = (
            per_time_count * 100.0 / expected if expected else np.full(count, np.nan)
        )
        minimum_per_time = int(np.nanmin(per_time_count))
        if minimum_per_time < observed:
            warnings.append(
                f"По отдельным срокам доступно от {minimum_per_time} из {expected} членов"
            )
    if observed < expected:
        warnings.append(f"Неполный ансамбль: {observed}/{expected} членов")
    _require_finite_field(fields, "temperature_2m", source)
    return MeteogramSeries(fields=fields, stats=stats, member_count=observed, expected_member_count=expected, warnings=warnings, **meta)


def _metadata(payload: dict[str, Any], source: MeteogramSource, point_label: str, lat: float, lon: float) -> dict[str, Any]:
    hourly = payload.get("hourly") or {}
    raw_times = hourly.get("time") or []
    if len(raw_times) < 2:
        reason = payload.get("reason") or payload.get("error") or "нет hourly.time"
        raise MeteogramError(f"Некорректный ответ {source.label}: {reason}")
    timezone_name = str(payload.get("timezone") or "UTC")
    metadata_warnings: list[str] = []
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        metadata_warnings.append(
            f"Не найден часовой пояс {timezone_name}; временная шкала показана в UTC"
        )
        timezone_name, tz = "UTC", timezone.utc
    times: list[datetime] = []
    previous_timestamp: float | None = None
    for value in raw_times:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise MeteogramError(f"Некорректный срок прогноза: {value}") from exc
        localized = _localize_monotonic(parsed, tz, previous_timestamp)
        times.append(localized)
        previous_timestamp = localized.timestamp()
    if any(
        right.timestamp() <= left.timestamp()
        for left, right in zip(times, times[1:])
    ):
        raise MeteogramError("Прогностические сроки не возрастают строго по времени")
    retrieved = _parse_time(payload.get("_retrieved_at_utc"))
    return {
        "source": source, "point_label": point_label,
        "requested_lat": float(lat), "requested_lon": float(lon),
        "grid_lat": _float_or_none(payload.get("latitude")),
        "grid_lon": _float_or_none(payload.get("longitude")),
        "timezone": timezone_name, "times": times,
        "retrieved_at_utc": retrieved, "warnings": [
            "Цикл исходной модели не передан поставщиком; показан период действия прогноза.",
            "Open-Meteo может интерполировать нативные сроки модели к почасовой сетке.",
            *metadata_warnings,
            *list(payload.get("_meteogram_warnings") or []),
        ],
    }


def _localize_monotonic(
    parsed: datetime, tz, previous_timestamp: float | None
) -> datetime:
    if parsed.tzinfo is not None:
        return parsed.astimezone(tz)
    candidates: list[datetime] = []
    seen_timestamps: set[float] = set()
    for fold in (0, 1):
        candidate = parsed.replace(tzinfo=tz, fold=fold)
        # Round-trip validation rejects nonexistent wall-clock times at the
        # spring DST transition and retains both folds of a repeated hour.
        roundtrip = (
            candidate.astimezone(timezone.utc)
            .astimezone(tz)
            .replace(tzinfo=None)
        )
        timestamp = candidate.timestamp()
        if roundtrip == parsed and timestamp not in seen_timestamps:
            candidates.append(candidate)
            seen_timestamps.add(timestamp)
    if not candidates:
        raise MeteogramError(f"Несуществующее местное время прогноза: {parsed.isoformat()}")
    candidates.sort(key=datetime.timestamp)
    if previous_timestamp is None:
        return candidates[0]
    return next(
        (candidate for candidate in candidates if candidate.timestamp() > previous_timestamp),
        candidates[0],
    )


def _require_finite_field(
    fields: dict[str, np.ndarray],
    parameter: str,
    source: MeteogramSource,
) -> None:
    values = np.asarray(fields.get(parameter, []), dtype=float)
    if np.isfinite(values).sum() < 2:
        raise MeteogramError(
            f"{source.label} не вернул пригодный ряд {parameter}"
        )


def _validate_payload_units(payload: dict[str, Any], source: MeteogramSource) -> None:
    units = payload.get("hourly_units") or {}
    if not isinstance(units, dict):
        units = {}
    warnings: list[str] = []
    for parameter, expected in EXPECTED_UNITS.items():
        raw = units.get(parameter)
        if raw is None:
            # Some ensemble responses expose units only on member fields.
            member_unit = next(
                (value for key, value in units.items() if str(key).startswith(parameter + "_member")),
                None,
            )
            raw = member_unit
        if raw is None:
            warnings.append(f"Источник не передал единицу поля {parameter}")
            continue
        normalised = str(raw).strip()
        if normalised not in expected:
            expected_text = "/".join(sorted(expected))
            raise MeteogramError(
                f"Неожиданная единица {parameter}: {normalised}; ожидалось {expected_text}"
            )
    payload["_meteogram_warnings"] = warnings

def _numeric(values: Any, count: int) -> np.ndarray:
    result = np.full(count, np.nan, dtype=float)
    if not isinstance(values, list):
        return result
    for index, value in enumerate(values[:count]):
        try:
            result[index] = float(value) if value is not None else np.nan
        except (TypeError, ValueError):
            pass
    return result


def _matrix(group: dict[int, np.ndarray], count: int) -> np.ndarray:
    return np.vstack([group[key] for key in sorted(group)]) if group else np.empty((0, count), dtype=float)


def _nanquantile(matrix: np.ndarray, q: float) -> np.ndarray:
    with np.errstate(all="ignore"):
        try:
            return np.nanquantile(matrix, q, axis=0, method="median_unbiased")
        except TypeError:
            return np.nanquantile(matrix, q, axis=0)


def _column_mode(matrix: np.ndarray) -> np.ndarray:
    result = np.full(matrix.shape[1], np.nan)
    for index in range(matrix.shape[1]):
        values = matrix[:, index]
        values = np.rint(values[np.isfinite(values)])
        if values.size:
            unique, counts = np.unique(values, return_counts=True)
            result[index] = unique[int(np.argmax(counts))]
    return result


def _circular_mean(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    radians = np.deg2rad(matrix)
    u, v = np.nanmean(np.cos(radians), axis=0), np.nanmean(np.sin(radians), axis=0)
    resultant = np.hypot(u, v)
    direction = (np.rad2deg(np.arctan2(v, u)) + 360.0) % 360.0
    direction[resultant < 0.05] = np.nan
    return direction, resultant


def _precip_intensity(values: np.ndarray, times: list[datetime]) -> np.ndarray:
    intervals = np.ones(len(times), dtype=float)
    if len(times) > 1:
        intervals[0] = max((times[1].timestamp() - times[0].timestamp()) / 3600.0, 0.01)
        for index in range(1, len(times)):
            intervals[index] = max(
                (times[index].timestamp() - times[index - 1].timestamp()) / 3600.0,
                0.01,
            )
    return np.asarray(values, dtype=float) / intervals


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
