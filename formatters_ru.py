from __future__ import annotations

import html
import math
import tempfile
from pathlib import Path

import pandas as pd

from gfs_core import DEFAULT_PROFILE_LEVELS_HPA, ProfileResult

PROFILE_LEVELS_HPA = DEFAULT_PROFILE_LEVELS_HPA
ISOTHERM_TARGETS_C = (0.0, -10.0, -20.0)


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


def _fmt_signed(value: float | None, width: int = 5) -> str:
    if value is None:
        return "  н/д"
    return f"{value:+{width}.1f}"


def _height_km(row: pd.Series) -> float:
    if "geopotential_height_km" in row:
        return float(row["geopotential_height_km"])
    return float(row["geopotential_height_m"]) / 1000.0


def _fmt_level_compact(row: pd.Series) -> str:
    pressure = int(round(float(row["pressure_hpa"])))
    temp = float(row["temperature_c"])
    dewpoint = float(row["dewpoint_c"]) if "dewpoint_c" in row else None
    humidity = int(round(float(row["relative_humidity_pct"])))
    wind_dir = int(round(float(row["wind_dir_deg"]))) % 360
    wind_speed = float(row["wind_speed_ms"])
    height_km = _height_km(row)
    return (
        f"{pressure:>4} {height_km:>4.1f} "
        f"{_fmt_signed(temp)}/{_fmt_signed(dewpoint)} "
        f"{humidity:>3} {wind_dir:03d}/{wind_speed:>4.1f}"
    )


def _isotherm_height_m(df: pd.DataFrame, target_c: float) -> float | None:
    if df.empty or "temperature_c" not in df or "geopotential_height_m" not in df:
        return None

    prof = df.sort_values("geopotential_height_m")[["temperature_c", "geopotential_height_m"]].dropna()
    if prof.empty:
        return None

    temps = prof["temperature_c"].to_numpy(dtype=float)
    heights = prof["geopotential_height_m"].to_numpy(dtype=float)

    for i in range(len(temps) - 1):
        t0, t1 = temps[i], temps[i + 1]
        h0, h1 = heights[i], heights[i + 1]
        if math.isclose(t0, target_c, abs_tol=0.05):
            return float(h0)
        if (t0 >= target_c >= t1) or (t0 <= target_c <= t1):
            if math.isclose(t0, t1, abs_tol=1e-9):
                return float(h0)
            ratio = (target_c - t0) / (t1 - t0)
            return float(h0 + ratio * (h1 - h0))

    if math.isclose(temps[-1], target_c, abs_tol=0.05):
        return float(heights[-1])
    return None


def _format_isotherms(result: ProfileResult) -> str:
    values = []
    for target in ISOTHERM_TARGETS_C:
        height_m = _isotherm_height_m(result.dataframe, target)
        values.append("н/д" if height_m is None else f"{height_m / 1000.0:.1f}")
    return f"❄ 0/-10/-20°C: {'/'.join(values)} км"


def _compact_table(result: ProfileResult) -> str:
    df = result.dataframe
    rows = ["pгПа zкм  T/Td°C     RH  Ветер", "---- ---- ----------- --- -------"]
    seen_pressures: set[int] = set()
    for level in PROFILE_LEVELS_HPA:
        row = _nearest_level_row(df, level)
        if row is None:
            continue
        pressure = int(round(float(row["pressure_hpa"])))
        if pressure in seen_pressures:
            continue
        seen_pressures.add(pressure)
        rows.append(_fmt_level_compact(row))
    return "\n".join(rows)


def format_profile_summary(result: ProfileResult) -> str:
    df = result.dataframe
    run_time = result.run.run_datetime_utc.strftime("%d.%m %HZ")
    valid_time = result.valid_time_utc.strftime("%d.%m %H:%M UTC")

    lines = [
        "🌦 <b>GFS 0.25</b> • профиль",
        f"🕓 {run_time} +{result.lead_hour}ч → {valid_time}",
        f"📍 {result.requested_lat:.3f},{result.requested_lon:.3f} → ⊞GFS {result.grid_lat:.3f},{result.grid_lon:.3f}",
        f"<pre>{html.escape(_compact_table(result))}</pre>",
    ]

    if not df.empty:
        max_wind_row = df.loc[df["wind_speed_ms"].idxmax()]
        max_wind = float(max_wind_row["wind_speed_ms"])
        max_wind_level = int(round(float(max_wind_row["pressure_hpa"])))
        max_wind_height_km = _height_km(max_wind_row)
        lines.extend(
            [
                _format_isotherms(result),
                f"🌬 max: {max_wind:.1f} м/с @ {max_wind_level} гПа ({max_wind_height_km:.1f} км)",
                f"📄 уровней: {len(df)}",
            ]
        )

    lines.append("ℹ NOMADS subset • GFS grid, не радиозонд")
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
