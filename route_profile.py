from __future__ import annotations

import csv
import math
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from aviation_style import risk_label
from cloudgram_product import CloudgramCell, _read_cloudgram_cell
from geocode import GeoPoint
from gfs_core import GfsProfileError, GfsRun, ProgressCallback, build_profile, canonical_leads

ROUTE_LEVELS_HPA = (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500)
ROUTE_TOP_HPA = 500
ROUTE_DEFAULT_SPEED_KMH = 300
ROUTE_MIN_SPEED_KMH = 50
ROUTE_MAX_SPEED_KMH = 1000
ROUTE_MAX_POINTS = 25


@dataclass(frozen=True)
class RouteWaypoint:
    index: int
    fraction: float
    lat: float
    lon: float
    distance_km: float
    elapsed_hours: float
    lead_hour: int
    valid_time_utc: datetime
    grid_lat: float
    grid_lon: float
    profile: pd.DataFrame
    surface: CloudgramCell
    risk_score: int
    risk_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RouteProfileData:
    run: GfsRun
    origin: GeoPoint
    destination: GeoPoint
    departure_lead: int
    speed_kmh: int
    mode: str
    total_distance_km: float
    duration_hours: float
    levels_hpa: tuple[int, ...]
    waypoints: tuple[RouteWaypoint, ...]
    temperature_c: np.ndarray
    humidity_pct: np.ndarray
    wind_speed_ms: np.ndarray
    wind_dir_deg: np.ndarray
    u_wind_ms: np.ndarray
    v_wind_ms: np.ndarray
    height_m: np.ndarray
    icing_score: np.ndarray
    turbulence_score: np.ndarray
    cloud_mask: np.ndarray
    point_risk: np.ndarray

    @property
    def arrival_lead(self) -> int:
        return int(self.waypoints[-1].lead_hour)

    @property
    def max_risk(self) -> int:
        return int(np.nanmax(self.point_risk)) if self.point_risk.size else 0


EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _to_unit(lat: float, lon: float) -> np.ndarray:
    p = math.radians(lat)
    l = math.radians(lon)
    return np.array([math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p)], dtype=float)


def _from_unit(vector: np.ndarray) -> tuple[float, float]:
    vector = vector / np.linalg.norm(vector)
    lat = math.degrees(math.asin(float(vector[2])))
    lon = math.degrees(math.atan2(float(vector[1]), float(vector[0])))
    return lat, lon


def great_circle_point(lat1: float, lon1: float, lat2: float, lon2: float, fraction: float) -> tuple[float, float]:
    fraction = max(0.0, min(1.0, float(fraction)))
    a = _to_unit(lat1, lon1)
    b = _to_unit(lat2, lon2)
    dot = max(-1.0, min(1.0, float(np.dot(a, b))))
    omega = math.acos(dot)
    if omega < 1e-9:
        return lat1, lon1
    if abs(math.pi - omega) < 1e-6:
        blended = (1.0 - fraction) * a + fraction * b
        if np.linalg.norm(blended) < 1e-9:
            blended = a
        return _from_unit(blended)
    sin_omega = math.sin(omega)
    vector = math.sin((1.0 - fraction) * omega) / sin_omega * a + math.sin(fraction * omega) / sin_omega * b
    return _from_unit(vector)


def normalize_eta_lead(raw_lead: float) -> int:
    allowed = canonical_leads()
    if raw_lead < 0 or raw_lead > allowed[-1]:
        raise GfsProfileError(f"Срок маршрута выходит за диапазон GFS 0…+{allowed[-1]} ч")
    return min(allowed, key=lambda value: (abs(value - raw_lead), value))


def route_waypoint_specs(
    origin: GeoPoint,
    destination: GeoPoint,
    departure_lead: int,
    speed_kmh: int = ROUTE_DEFAULT_SPEED_KMH,
    max_points: int = ROUTE_MAX_POINTS,
) -> tuple[float, float, list[tuple[float, float, float, float, int]]]:
    if not ROUTE_MIN_SPEED_KMH <= int(speed_kmh) <= ROUTE_MAX_SPEED_KMH:
        raise GfsProfileError(f"Скорость маршрута должна быть {ROUTE_MIN_SPEED_KMH}…{ROUTE_MAX_SPEED_KMH} км/ч")
    distance = haversine_km(origin.lat, origin.lon, destination.lat, destination.lon)
    if distance < 5.0:
        raise GfsProfileError("Начало и конец маршрута почти совпадают; задайте маршрут длиннее 5 км")
    duration = distance / float(speed_kmh)
    max_points = max(2, int(max_points))
    interval_h = max(1.0, duration / float(max_points - 1))
    elapsed_values = [0.0]
    current = interval_h
    while current < duration - 1e-9:
        elapsed_values.append(current)
        current += interval_h
    elapsed_values.append(duration)

    specs: list[tuple[float, float, float, float, int]] = []
    for elapsed in elapsed_values:
        fraction = 0.0 if duration <= 0 else elapsed / duration
        lat, lon = great_circle_point(origin.lat, origin.lon, destination.lat, destination.lon, fraction)
        lead = normalize_eta_lead(float(departure_lead) + elapsed)
        specs.append((fraction, lat, lon, distance * fraction, lead))
    return distance, duration, specs


def _row_for_level(profile: pd.DataFrame, level: int) -> pd.Series | None:
    if profile.empty:
        return None
    selected = profile[profile["pressure_hpa"] >= ROUTE_TOP_HPA].copy()
    if selected.empty:
        return None
    index = (selected["pressure_hpa"] - float(level)).abs().idxmin()
    row = selected.loc[index]
    if abs(float(row["pressure_hpa"]) - level) > 30:
        return None
    return row


def _icing_severity(temperature_c: float, rh_pct: float) -> int:
    if not (-20.0 <= temperature_c <= 0.5 and rh_pct >= 80.0):
        return 0
    score = 1
    if -15.0 <= temperature_c <= -3.0 and rh_pct >= 90.0:
        score = 2
    if -12.0 <= temperature_c <= -5.0 and rh_pct >= 95.0:
        score = 3
    return score


def _shear_severity(shear_ms_per_km: float) -> int:
    if shear_ms_per_km >= 10.0:
        return 3
    if shear_ms_per_km >= 6.0:
        return 2
    if shear_ms_per_km >= 4.0:
        return 1
    return 0


def _surface_risk(cell: CloudgramCell) -> int:
    if cell.hazard_score >= 3:
        return 3
    return max(0, min(2, int(cell.hazard_score)))


def _finite_max(values: np.ndarray, default: float = 0.0) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    return float(np.max(finite)) if finite.size else float(default)


def _point_reasons(
    surface: CloudgramCell,
    icing: np.ndarray,
    turbulence: np.ndarray,
    wind_speed: np.ndarray,
    humidity: np.ndarray,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if surface.phenomena and surface.phenomena != "—":
        reasons.append(surface.phenomena)
    if surface.cb_score >= 2:
        reasons.append("конвекция/гроза")
    if surface.visibility_km is not None and surface.visibility_km < 5:
        reasons.append("видимость <5 км")
    if surface.ceiling_m is not None and surface.ceiling_m < 1000:
        reasons.append("низкий ВНГО")
    if _finite_max(icing) >= 2:
        reasons.append("обледенение")
    elif _finite_max(icing) >= 1:
        reasons.append("риск обледенения")
    if _finite_max(turbulence) >= 2:
        reasons.append("сильный сдвиг/турбулентность")
    elif _finite_max(turbulence) >= 1:
        reasons.append("сдвиг ветра")
    if _finite_max(wind_speed) >= 30:
        reasons.append("ветер ≥30 м/с")
    elif _finite_max(wind_speed) >= 20:
        reasons.append("сильный ветер")
    if _finite_max(humidity) >= 90:
        reasons.append("облачный слой")
    if not reasons:
        reasons.append("значимых модельных рисков нет")
    return tuple(dict.fromkeys(reasons))


def build_route_profile_data(
    run: GfsRun,
    origin: GeoPoint,
    destination: GeoPoint,
    departure_lead: int,
    speed_kmh: int = ROUTE_DEFAULT_SPEED_KMH,
    mode: str = "simple",
    progress_callback: ProgressCallback | None = None,
    max_points: int = ROUTE_MAX_POINTS,
) -> RouteProfileData:
    mode = mode.lower().strip()
    if mode not in {"simple", "pro"}:
        raise GfsProfileError("Режим маршрута: simple или pro")
    distance, duration, specs = route_waypoint_specs(origin, destination, departure_lead, speed_kmh, max_points=max_points)
    max_lead = max(item[4] for item in specs)
    if max_lead > 384:
        raise GfsProfileError("Маршрут заканчивается позже доступного диапазона GFS +384 ч")

    n_levels = len(ROUTE_LEVELS_HPA)
    n_points = len(specs)
    arrays = {name: np.full((n_levels, n_points), np.nan, dtype=float) for name in (
        "temperature", "humidity", "wind_speed", "wind_dir", "u", "v", "height"
    )}
    icing = np.zeros((n_levels, n_points), dtype=int)
    turbulence = np.zeros((n_levels, n_points), dtype=int)
    cloud_mask = np.zeros((n_levels, n_points), dtype=bool)
    waypoints: list[RouteWaypoint] = []
    point_risk = np.zeros(n_points, dtype=int)

    for point_index, (fraction, lat, lon, distance_km, lead_hour) in enumerate(specs):
        if progress_callback:
            progress_callback({
                "stage": "route_step",
                "message": f"точка {point_index + 1}/{n_points} · {distance_km:.0f} км · ETA +{lead_hour} ч",
                "index": point_index + 1,
                "total": n_points,
                "lead_hour": lead_hour,
            })
        profile_result = build_profile(run, lead_hour, lat, lon, progress_callback=None)
        surface, _, _, _ = _read_cloudgram_cell(
            run,
            lead_hour,
            lat,
            lon,
            progress_callback=None,
            duration_hours=1,
        )
        profile = profile_result.dataframe[profile_result.dataframe["pressure_hpa"] >= ROUTE_TOP_HPA].copy()

        for level_index, level in enumerate(ROUTE_LEVELS_HPA):
            row = _row_for_level(profile, level)
            if row is None:
                continue
            arrays["temperature"][level_index, point_index] = float(row["temperature_c"])
            arrays["humidity"][level_index, point_index] = float(row["relative_humidity_pct"])
            arrays["wind_speed"][level_index, point_index] = float(row["wind_speed_ms"])
            arrays["wind_dir"][level_index, point_index] = float(row["wind_dir_deg"])
            arrays["u"][level_index, point_index] = float(row["u_wind_ms"])
            arrays["v"][level_index, point_index] = float(row["v_wind_ms"])
            arrays["height"][level_index, point_index] = float(row["geopotential_height_m"])
            icing[level_index, point_index] = _icing_severity(
                arrays["temperature"][level_index, point_index],
                arrays["humidity"][level_index, point_index],
            )
            cloud_mask[level_index, point_index] = arrays["humidity"][level_index, point_index] >= 80.0

        for level_index in range(n_levels - 1):
            u0, u1 = arrays["u"][level_index : level_index + 2, point_index]
            v0, v1 = arrays["v"][level_index : level_index + 2, point_index]
            z0, z1 = arrays["height"][level_index : level_index + 2, point_index]
            if not np.all(np.isfinite([u0, u1, v0, v1, z0, z1])):
                continue
            dz_km = abs(z1 - z0) / 1000.0
            if dz_km < 0.05:
                continue
            shear = math.hypot(u1 - u0, v1 - v0) / dz_km
            severity = _shear_severity(shear)
            turbulence[level_index, point_index] = max(turbulence[level_index, point_index], severity)
            turbulence[level_index + 1, point_index] = max(turbulence[level_index + 1, point_index], severity)

        vertical_risk = 0
        if _finite_max(icing[:, point_index]) >= 3 or _finite_max(turbulence[:, point_index]) >= 3:
            vertical_risk = 3
        elif _finite_max(icing[:, point_index]) >= 2 or _finite_max(turbulence[:, point_index]) >= 2 or _finite_max(arrays["wind_speed"][:, point_index]) >= 30:
            vertical_risk = 2
        elif _finite_max(icing[:, point_index]) >= 1 or _finite_max(turbulence[:, point_index]) >= 1 or _finite_max(arrays["wind_speed"][:, point_index]) >= 20:
            vertical_risk = 1
        convection_risk = 3 if surface.cb_score >= 2 else min(2, int(surface.cb_score))
        point_score = max(_surface_risk(surface), vertical_risk, convection_risk)
        point_risk[point_index] = point_score
        reasons = _point_reasons(
            surface,
            icing[:, point_index],
            turbulence[:, point_index],
            arrays["wind_speed"][:, point_index],
            arrays["humidity"][:, point_index],
        )
        waypoints.append(
            RouteWaypoint(
                index=point_index,
                fraction=fraction,
                lat=lat,
                lon=lon,
                distance_km=distance_km,
                elapsed_hours=duration * fraction,
                lead_hour=lead_hour,
                valid_time_utc=run.run_datetime_utc + timedelta(hours=lead_hour),
                grid_lat=profile_result.grid_lat,
                grid_lon=profile_result.grid_lon,
                profile=profile,
                surface=surface,
                risk_score=point_score,
                risk_reasons=reasons,
            )
        )

    if progress_callback:
        progress_callback({"stage": "route_done", "message": f"маршрутный профиль готов: {n_points} точек"})
    return RouteProfileData(
        run=run,
        origin=origin,
        destination=destination,
        departure_lead=int(departure_lead),
        speed_kmh=int(speed_kmh),
        mode=mode,
        total_distance_km=distance,
        duration_hours=duration,
        levels_hpa=ROUTE_LEVELS_HPA,
        waypoints=tuple(waypoints),
        temperature_c=arrays["temperature"],
        humidity_pct=arrays["humidity"],
        wind_speed_ms=arrays["wind_speed"],
        wind_dir_deg=arrays["wind_dir"],
        u_wind_ms=arrays["u"],
        v_wind_ms=arrays["v"],
        height_m=arrays["height"],
        icing_score=icing,
        turbulence_score=turbulence,
        cloud_mask=cloud_mask,
        point_risk=point_risk,
    )


def route_summary(data: RouteProfileData) -> str:
    counts = {score: int(np.sum(data.point_risk == score)) for score in range(4)}
    worst_points = [point for point in data.waypoints if point.risk_score == data.max_risk]
    worst = worst_points[0] if worst_points else data.waypoints[0]
    reasons = ", ".join(worst.risk_reasons[:4])
    return (
        f"✈️ GFS 0.25 · маршрутный профиль · {'Профи' if data.mode == 'pro' else 'Простой'}\n"
        f"🧭 {data.origin.label} → {data.destination.label}\n"
        f"🕒 run {data.run.date} {data.run.cycle}Z · вылет +{data.departure_lead} ч · прибытие около +{data.arrival_lead} ч\n"
        f"📏 {data.total_distance_km:.0f} км · {data.duration_hours:.1f} ч · {data.speed_kmh} км/ч · точек {len(data.waypoints)}\n"
        f"🚦 {risk_label(data.max_risk)} · худший участок около {worst.distance_km:.0f} км: {reasons}\n"
        f"🟢/🟡/🟠/🔴 точек: {counts[0]}/{counts[1]}/{counts[2]}/{counts[3]}\n"
        "ℹ Модельная диагностика GFS, не авиационное разрешение. Проверяйте METAR/TAF/SIGMET/NOTAM и решение командира."
    )


def route_csv_text(data: RouteProfileData) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "point", "distance_km", "elapsed_h", "lead_h", "valid_utc", "lat", "lon", "grid_lat", "grid_lon",
        "pressure_hpa", "height_m", "temperature_c", "rh_pct", "wind_speed_ms", "wind_dir_deg",
        "icing_score", "turbulence_score", "surface_phenomena", "visibility_km", "ceiling_m", "cape_jkg",
        "cb_score", "route_risk_score", "route_risk_label", "risk_reasons",
    ])
    for point in data.waypoints:
        for level_index, level in enumerate(data.levels_hpa):
            writer.writerow([
                point.index,
                f"{point.distance_km:.1f}",
                f"{point.elapsed_hours:.2f}",
                point.lead_hour,
                point.valid_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                f"{point.lat:.5f}",
                f"{point.lon:.5f}",
                f"{point.grid_lat:.3f}",
                f"{point.grid_lon:.3f}",
                level,
                _csv_value(data.height_m[level_index, point.index], 0),
                _csv_value(data.temperature_c[level_index, point.index], 1),
                _csv_value(data.humidity_pct[level_index, point.index], 0),
                _csv_value(data.wind_speed_ms[level_index, point.index], 1),
                _csv_value(data.wind_dir_deg[level_index, point.index], 0),
                int(data.icing_score[level_index, point.index]),
                int(data.turbulence_score[level_index, point.index]),
                point.surface.phenomena,
                "" if point.surface.visibility_km is None else f"{point.surface.visibility_km:.1f}",
                "" if point.surface.ceiling_m is None else f"{point.surface.ceiling_m:.0f}",
                "" if point.surface.cape_jkg is None else f"{point.surface.cape_jkg:.0f}",
                point.surface.cb_score,
                point.risk_score,
                risk_label(point.risk_score, short=True),
                "; ".join(point.risk_reasons),
            ])
    return output.getvalue()


def _csv_value(value: float, digits: int) -> str:
    return "" if not np.isfinite(value) else f"{float(value):.{digits}f}"


def write_route_csv(data: RouteProfileData) -> Path:
    suffix = f"_{data.run.date}_{data.run.cycle}_f{data.departure_lead:03d}_{data.speed_kmh}kmh.csv"
    tmp = tempfile.NamedTemporaryFile(prefix="gfs_route_profile", suffix=suffix, delete=False)
    path = Path(tmp.name)
    tmp.close()
    path.write_text(route_csv_text(data), encoding="utf-8-sig")
    return path
