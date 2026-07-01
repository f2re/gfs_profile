from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from gfs_core import DEFAULT_PROFILE_LEVELS_HPA, ProfileResult, freezing_level_diagnostic

PROFILE_LEVELS_HPA = DEFAULT_PROFILE_LEVELS_HPA


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
    dewpoint = float(row["dewpoint_c"]) if "dewpoint_c" in row else None
    humidity = int(round(float(row["relative_humidity_pct"])))
    wind_dir = int(round(float(row["wind_dir_deg"]))) % 360
    wind_speed = float(row["wind_speed_ms"])
    height_km = float(row.get("geopotential_height_km", float(row["geopotential_height_m"]) / 1000.0))
    dewpoint_part = f" | точка росы {dewpoint:+4.1f} °C" if dewpoint is not None else ""
    return (
        f"{pressure:>4} гПа | {height_km:>4.1f} км | температура {temp:+5.1f} °C"
        f"{dewpoint_part} | влажн. {humidity:>3}% | ветер {wind_dir:03d}° / {wind_speed:>4.1f} м/с"
    )


def _format_freezing_level(result: ProfileResult) -> str:
    diagnostic = freezing_level_diagnostic(result.dataframe)
    if diagnostic["status"] == "found":
        height_m = float(diagnostic["height_m"])
        return f"Уровень 0 °C: {height_m / 1000.0:.1f} км ({height_m:.0f} м)"
    if diagnostic["status"] == "below_lowest_level":
        return "Уровень 0 °C: ниже нижнего доступного уровня"
    if diagnostic["status"] == "above_highest_level":
        return "Уровень 0 °C: выше верхнего доступного уровня"
    return "Уровень 0 °C: не определяется по профилю"


def format_profile_summary(result: ProfileResult) -> str:
    df = result.dataframe
    run_time = result.run.run_datetime_utc.strftime("%Y-%m-%d %HZ")
    valid_time = result.valid_time_utc.strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "GFS 0.25: модельный профиль атмосферы",
        f"Запуск: {run_time} | срок: +{result.lead_hour} ч",
        f"Действительно на: {valid_time}",
        f"Запрошено: {result.requested_lat:.4f}, {result.requested_lon:.4f}",
        f"Узел GFS: {result.grid_lat:.3f}, {result.grid_lon:.3f}",
        "",
        "Профиль по уровням:",
    ]

    seen_pressures: set[int] = set()
    for level in PROFILE_LEVELS_HPA:
        row = _nearest_level_row(df, level)
        if row is not None:
            pressure = int(round(float(row["pressure_hpa"])))
            if pressure in seen_pressures:
                continue
            seen_pressures.add(pressure)
            lines.append(_fmt_level(row))

    if not df.empty:
        max_wind_row = df.loc[df["wind_speed_ms"].idxmax()]
        max_wind = float(max_wind_row["wind_speed_ms"])
        max_wind_level = int(round(float(max_wind_row["pressure_hpa"])))
        max_wind_height_km = float(max_wind_row.get("geopotential_height_km", float(max_wind_row["geopotential_height_m"]) / 1000.0))
        lines.extend(
            [
                "",
                "Особые точки:",
                _format_freezing_level(result),
                f"Макс. ветер: {max_wind:.1f} м/с на {max_wind_level} гПа ({max_wind_height_km:.1f} км)",
                f"Уровней профиля: {len(df)}",
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
    try:
        result.dataframe.round(3).to_csv(out_path, index=False)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path
