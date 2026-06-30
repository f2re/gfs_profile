from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from gfs_core import ProfileResult, freezing_level_m

KEY_LEVELS_HPA = (1000, 925, 850, 700, 500, 300)


def _nearest_level_row(df: pd.DataFrame, pressure_hpa: int) -> pd.Series | None:
    if df.empty or "pressure_hpa" not in df:
        return None
    idx = (df["pressure_hpa"] - pressure_hpa).abs().idxmin()
    row = df.loc[idx]
    if abs(float(row["pressure_hpa"]) - pressure_hpa) > 35:
        return None
    return row


def _fmt_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "н/д"
    return f"{value:.{digits}f}"


def _fmt_level(row: pd.Series) -> str:
    pressure = int(round(float(row["pressure_hpa"])))
    temp = float(row["temperature_c"])
    rh = int(round(float(row["relative_humidity_pct"])))
    wind_dir = int(round(float(row["wind_dir_deg"]))) % 360
    wind_speed = float(row["wind_speed_ms"])
    height = int(round(float(row["geopotential_height_m"])))
    return f"{pressure:>4} гПа | {height:>5} м | {temp:+5.1f} °C | RH {rh:>3}% | {wind_dir:03d}° {wind_speed:>4.1f} м/с"


def format_profile_summary(result: ProfileResult) -> str:
    df = result.dataframe
    run_time = result.run.run_datetime_utc.strftime("%Y-%m-%d %HZ")
    valid_time = result.valid_time_utc.strftime("%Y-%m-%d %H:%M UTC")
    zero_level = freezing_level_m(df)

    lines = [
        "GFS 0.25° профиль атмосферы",
        f"Run: {run_time} | Lead: +{result.lead_hour} ч",
        f"Valid: {valid_time}",
        f"Запрошено: {result.requested_lat:.4f}, {result.requested_lon:.4f}",
        f"Узел GFS: {result.grid_lat:.3f}, {result.grid_lon:.3f}",
        "",
        "Ключевые уровни:",
    ]

    for level in KEY_LEVELS_HPA:
        row = _nearest_level_row(df, level)
        if row is not None:
            lines.append(_fmt_level(row))

    if not df.empty:
        max_wind_row = df.loc[df["wind_speed_ms"].idxmax()]
        max_wind = float(max_wind_row["wind_speed_ms"])
        max_wind_level = int(round(float(max_wind_row["pressure_hpa"])))
        max_wind_height = int(round(float(max_wind_row["geopotential_height_m"])))
        lines.extend(
            [
                "",
                f"0 °C: {_fmt_float(zero_level, 0)} м" if zero_level is not None else "0 °C: не найден в профиле",
                f"Max wind: {max_wind:.1f} м/с на {max_wind_level} гПа ({max_wind_height} м)",
                f"Строк профиля: {len(df)}",
            ]
        )

    lines.append("")
    lines.append("Источник: NOMADS GRIB Filter. Это модельная точка GFS, не радиозонд.")
    return "\n".join(lines)


def write_profile_csv(result: ProfileResult) -> Path:
    suffix = f"_{result.run.date}_{result.run.cycle}_f{result.lead_hour:03d}_{result.grid_lat:.3f}_{result.grid_lon:.3f}.csv"
    safe_suffix = suffix.replace("-", "m").replace(" ", "_")
    out = tempfile.NamedTemporaryFile(prefix="gfs_profile", suffix=safe_suffix, delete=False)
    out_path = Path(out.name)
    out.close()
    result.dataframe.round(3).to_csv(out_path, index=False)
    return out_path
