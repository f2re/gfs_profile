from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from gfs_core import GfsProfileError, GfsRun, ProfileResult, ProgressCallback, canonical_leads
from gfs_product_core import build_profile_for_levels

WINDGRAM_LEVELS_HPA = (
    1000,
    975,
    950,
    925,
    900,
    850,
    800,
    750,
    700,
    650,
    600,
    550,
    500,
)
WINDGRAM_PARAMS = ("wind", "temp", "rh")
WINDGRAM_PARAM_ALIASES = {
    "wind": "wind",
    "ветер": "wind",
    "v": "wind",
    "speed": "wind",
    "temp": "temp",
    "t": "temp",
    "temperature": "temp",
    "температура": "temp",
    "rh": "rh",
    "humidity": "rh",
    "влажность": "rh",
}


@dataclass(frozen=True)
class WindgramCell:
    lead_hour: int
    valid_time_utc: datetime
    pressure_hpa: int
    height_m: float | None
    temperature_c: float | None
    relative_humidity_pct: float | None
    u_wind_ms: float | None
    v_wind_ms: float | None
    wind_speed_ms: float | None
    wind_dir_deg: float | None


@dataclass(frozen=True)
class WindgramData:
    run: GfsRun
    requested_lat: float
    requested_lon: float
    grid_lat: float
    grid_lon: float
    leads: list[int]
    levels_hpa: list[int]
    cells: list[WindgramCell]
    param: str = "wind"

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([cell.__dict__ for cell in self.cells])


def normalize_windgram_param(param: str | None) -> str:
    value = (param or "wind").strip().lower()
    normalized = WINDGRAM_PARAM_ALIASES.get(value)
    if not normalized:
        raise GfsProfileError("param для windgram должен быть wind, temp или rh")
    return normalized


def windgram_leads(lead_from: int = 0, lead_to: int = 120, step: int = 6) -> list[int]:
    if step <= 0:
        raise GfsProfileError("Шаг windgram должен быть положительным")
    if lead_from < 0 or lead_to < lead_from:
        raise GfsProfileError("Некорректный диапазон сроков windgram")
    allowed = set(canonical_leads())
    leads = [lead for lead in range(lead_from, lead_to + 1, step) if lead in allowed]
    if not leads:
        raise GfsProfileError("В диапазоне windgram нет допустимых сроков GFS")
    return leads


def _nearest_level_row(df: pd.DataFrame, level_hpa: int, tolerance_hpa: float = 35.0) -> pd.Series | None:
    if df.empty or "pressure_hpa" not in df:
        return None
    idx = (df["pressure_hpa"] - level_hpa).abs().idxmin()
    row = df.loc[idx]
    if abs(float(row["pressure_hpa"]) - level_hpa) > tolerance_hpa:
        return None
    return row


def _nullable_float(row: pd.Series, name: str) -> float | None:
    if name not in row or pd.isna(row[name]):
        return None
    return float(row[name])


def _cell_from_profile(result: ProfileResult, level_hpa: int) -> WindgramCell:
    row = _nearest_level_row(result.dataframe, level_hpa)
    if row is None:
        return WindgramCell(
            lead_hour=result.lead_hour,
            valid_time_utc=result.valid_time_utc,
            pressure_hpa=level_hpa,
            height_m=None,
            temperature_c=None,
            relative_humidity_pct=None,
            u_wind_ms=None,
            v_wind_ms=None,
            wind_speed_ms=None,
            wind_dir_deg=None,
        )
    return WindgramCell(
        lead_hour=result.lead_hour,
        valid_time_utc=result.valid_time_utc,
        pressure_hpa=level_hpa,
        height_m=_nullable_float(row, "geopotential_height_m"),
        temperature_c=_nullable_float(row, "temperature_c"),
        relative_humidity_pct=_nullable_float(row, "relative_humidity_pct"),
        u_wind_ms=_nullable_float(row, "u_wind_ms"),
        v_wind_ms=_nullable_float(row, "v_wind_ms"),
        wind_speed_ms=_nullable_float(row, "wind_speed_ms"),
        wind_dir_deg=_nullable_float(row, "wind_dir_deg"),
    )


def build_windgram_data(
    run: GfsRun,
    lat: float,
    lon: float,
    lead_from: int = 0,
    lead_to: int = 120,
    step: int = 6,
    top_hpa: int = 500,
    param: str = "wind",
    progress_callback: ProgressCallback | None = None,
) -> WindgramData:
    selected_param = normalize_windgram_param(param)
    leads = windgram_leads(lead_from=lead_from, lead_to=lead_to, step=step)
    levels = [level for level in WINDGRAM_LEVELS_HPA if level >= top_hpa]
    if not levels:
        raise GfsProfileError("Нет уровней windgram для указанного top_hpa")

    profiles: list[ProfileResult] = []
    total = len(leads)
    for index, lead_hour in enumerate(leads, start=1):
        if progress_callback:
            progress_callback({"stage": "windgram_step", "message": "готовлю профиль", "index": index, "total": total, "lead_hour": lead_hour})
        result = build_profile_for_levels(
            run,
            lead_hour,
            lat,
            lon,
            levels_hpa=tuple(levels),
            progress_callback=progress_callback,
        )
        profiles.append(result)

    if not profiles:
        raise GfsProfileError("Windgram не построен: нет профилей")

    cells: list[WindgramCell] = []
    for result in profiles:
        for level in levels:
            cells.append(_cell_from_profile(result, level))

    first = profiles[0]
    return WindgramData(
        run=run,
        requested_lat=lat,
        requested_lon=lon,
        grid_lat=first.grid_lat,
        grid_lon=first.grid_lon,
        leads=leads,
        levels_hpa=levels,
        cells=cells,
        param=selected_param,
    )


def windgram_matrices(data: WindgramData) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return speed, direction, u, v, temperature and RH matrices with shape levels x leads.

    Rows follow data.levels_hpa order: 1000 ... 500 hPa.
    """

    frame = data.to_frame()
    n_levels = len(data.levels_hpa)
    n_leads = len(data.leads)
    speed = np.full((n_levels, n_leads), np.nan, dtype=float)
    direction = np.full((n_levels, n_leads), np.nan, dtype=float)
    u = np.full((n_levels, n_leads), np.nan, dtype=float)
    v = np.full((n_levels, n_leads), np.nan, dtype=float)
    temperature = np.full((n_levels, n_leads), np.nan, dtype=float)
    humidity = np.full((n_levels, n_leads), np.nan, dtype=float)

    level_index = {level: idx for idx, level in enumerate(data.levels_hpa)}
    lead_index = {lead: idx for idx, lead in enumerate(data.leads)}
    for _, row in frame.iterrows():
        i = level_index[int(row["pressure_hpa"])]
        j = lead_index[int(row["lead_hour"])]
        if pd.notna(row["wind_speed_ms"]):
            speed[i, j] = float(row["wind_speed_ms"])
        if pd.notna(row["wind_dir_deg"]):
            direction[i, j] = float(row["wind_dir_deg"])
        if pd.notna(row["u_wind_ms"]):
            u[i, j] = float(row["u_wind_ms"])
        if pd.notna(row["v_wind_ms"]):
            v[i, j] = float(row["v_wind_ms"])
        if pd.notna(row["temperature_c"]):
            temperature[i, j] = float(row["temperature_c"])
        if pd.notna(row["relative_humidity_pct"]):
            humidity[i, j] = float(row["relative_humidity_pct"])
    return speed, direction, u, v, temperature, humidity
