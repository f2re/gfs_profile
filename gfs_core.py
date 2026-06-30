from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests
from requests import RequestException

NOMADS_BASE = os.getenv("NOMADS_BASE", "https://nomads.ncep.noaa.gov")
REQUEST_TIMEOUT = int(os.getenv("GFS_REQUEST_TIMEOUT", os.getenv("REQUEST_TIMEOUT", "35")))
CACHE_TTL_SECONDS = int(os.getenv("GFS_CACHE_TTL_SECONDS", str(24 * 3600)))
CACHE_DIR = Path(os.getenv("GFS_CACHE_DIR", os.getenv("CACHE_DIR", ".cache_gfs")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
GRID_STEP_DEG = 0.25


class GfsProfileError(RuntimeError):
    """Operational error while downloading or parsing a GFS profile."""


@dataclass(frozen=True)
class GfsRun:
    date: str
    cycle: str

    @property
    def run_datetime_utc(self) -> datetime:
        return datetime.strptime(f"{self.date}{self.cycle}", "%Y%m%d%H").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ProfileResult:
    run: GfsRun
    lead_hour: int
    requested_lat: float
    requested_lon: float
    grid_lat: float
    grid_lon: float
    grib_path: Path
    dataframe: pd.DataFrame

    @property
    def valid_time_utc(self) -> datetime:
        return self.run.run_datetime_utc + timedelta(hours=self.lead_hour)

    def to_payload(self) -> dict[str, Any]:
        df = self.dataframe
        return {
            "meta": {
                "date": self.run.date,
                "cycle": self.run.cycle,
                "lead_index": self.lead_hour,
                "lead_hour": self.lead_hour,
                "valid_time_utc": self.valid_time_utc.strftime("%Y-%m-%d %H:%M"),
                "requested_point": {"lat": self.requested_lat, "lon": self.requested_lon},
                "gfs_grid_point": {"lat": self.grid_lat, "lon": self.grid_lon},
                "max_height_m": float(df["geopotential_height_m"].max()) if not df.empty else 0.0,
                "source": "NOMADS GRIB Filter + disk cache",
                "rows": int(len(df)),
                "cache_file": self.grib_path.name,
                "freezing_level": freezing_level_diagnostic(df),
            },
            "columns": list(df.columns),
            "rows": df.round(3).to_dict(orient="records"),
        }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_leads() -> list[int]:
    return list(range(0, 121)) + list(range(123, 385, 3))


def validate_lead(lead_hour: int) -> int:
    if lead_hour not in canonical_leads():
        raise GfsProfileError("lead_hour вне допустимого диапазона GFS: 0-120 каждый час, 123-384 каждые 3 часа")
    return lead_hour


def snap_to_gfs_grid(lat: float, lon: float) -> tuple[float, float]:
    if not -90 <= lat <= 90:
        raise GfsProfileError("Широта должна быть в диапазоне -90..90")
    if not -180 <= lon <= 180:
        raise GfsProfileError("Долгота должна быть в диапазоне -180..180")

    grid_lat = round(lat / GRID_STEP_DEG) * GRID_STEP_DEG
    grid_lon = round(lon / GRID_STEP_DEG) * GRID_STEP_DEG
    grid_lat = max(-90.0, min(90.0, grid_lat))
    if grid_lon > 180:
        grid_lon -= 360
    if grid_lon < -180:
        grid_lon += 360
    return round(grid_lat, 3), round(grid_lon, 3)


def run_file_name(cycle: str, lead_hour: int) -> str:
    return f"gfs.t{cycle}z.pgrb2.0p25.f{lead_hour:03d}"


def run_dir(date: str, cycle: str) -> str:
    return f"/gfs.{date}/{cycle}/atmos"


def source_idx_url(date: str, cycle: str, lead_hour: int = 0) -> str:
    file_name = run_file_name(cycle, lead_hour)
    return f"{NOMADS_BASE}/pub/data/nccf/com/gfs/prod/gfs.{date}/{cycle}/atmos/{file_name}.idx"


def cache_key(date: str, cycle: str, lead_hour: int, lat: float, lon: float) -> str:
    return f"{date}_{cycle}_f{lead_hour:03d}_{lat:.3f}_{lon:.3f}".replace("-", "m")


def grib_filter_url(date: str, cycle: str, lead_hour: int, lat: float, lon: float) -> str:
    lon_360 = lon % 360
    top_lat = min(90.0, lat + 0.001)
    bottom_lat = max(-90.0, lat)
    query = {
        "file": run_file_name(cycle, lead_hour),
        "all_lev": "on",
        "var_TMP": "on",
        "var_RH": "on",
        "var_UGRD": "on",
        "var_VGRD": "on",
        "var_HGT": "on",
        "leftlon": f"{lon_360:.3f}",
        "rightlon": f"{lon_360 + 0.001:.3f}",
        "toplat": f"{top_lat:.3f}",
        "bottomlat": f"{bottom_lat:.3f}",
        "dir": run_dir(date, cycle),
    }
    return f"{NOMADS_BASE}/cgi-bin/filter_gfs_0p25_1hr.pl?{urlencode(query)}"


@lru_cache(maxsize=4096)
def forecast_file_exists(date: str, cycle: str, lead_hour: int) -> bool:
    """Check that the exact GFS forecast file for this cycle and lead is published."""

    validate_lead(lead_hour)
    try:
        response = requests.head(source_idx_url(date, cycle, lead_hour), timeout=12)
        return response.status_code == 200
    except RequestException:
        return False


@lru_cache(maxsize=256)
def cycle_exists(date: str, cycle: str) -> bool:
    return forecast_file_exists(date, cycle, 0)


def latest_available_run_for_lead(lead_hour: int, reference_time: datetime | None = None) -> GfsRun:
    """Return the newest GFS run where the requested forecast lead is already available."""

    validate_lead(lead_hour)
    ref = reference_time or now_utc()
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    ref = ref.astimezone(timezone.utc)

    for day_offset in (0, 1, 2):
        day = ref.date() - timedelta(days=day_offset)
        date = day.strftime("%Y%m%d")
        for cycle in ("18", "12", "06", "00"):
            run_time = datetime.strptime(f"{date}{cycle}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
            if run_time <= ref and forecast_file_exists(date, cycle, lead_hour):
                return GfsRun(date=date, cycle=cycle)

    raise GfsProfileError(f"Не найден доступный цикл GFS со сроком +{lead_hour} ч за последние 3 дня")


def latest_available_run(reference_time: datetime | None = None) -> GfsRun:
    return latest_available_run_for_lead(0, reference_time=reference_time)


def clean_old_cache() -> None:
    cutoff = time.time() - CACHE_TTL_SECONDS
    for path in CACHE_DIR.glob("*.grib2"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def download_profile_grib_to_disk(date: str, cycle: str, lead_hour: int, lat: float, lon: float) -> Path:
    validate_lead(lead_hour)
    clean_old_cache()
    key = cache_key(date, cycle, lead_hour, lat, lon)
    out_path = CACHE_DIR / f"{key}.grib2"
    if out_path.exists():
        return out_path

    if not forecast_file_exists(date, cycle, lead_hour):
        raise GfsProfileError(f"Файл GFS для {date} {cycle}Z +{lead_hour} ч ещё не опубликован")

    url = grib_filter_url(date, cycle, lead_hour, lat, lon)
    part_path = CACHE_DIR / f"{key}.part"
    try:
        with requests.get(url, timeout=REQUEST_TIMEOUT, stream=True) as response:
            if response.status_code != 200:
                raise GfsProfileError(f"Ошибка загрузки GFS: HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                raise GfsProfileError("NOMADS вернул HTML вместо GRIB2")

            with part_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        file_obj.write(chunk)
    except RequestException as exc:
        part_path.unlink(missing_ok=True)
        raise GfsProfileError(f"Ошибка подключения к NOMADS: {exc}") from exc
    except Exception:
        part_path.unlink(missing_ok=True)
        raise

    if not part_path.exists() or part_path.stat().st_size < 256:
        part_path.unlink(missing_ok=True)
        raise GfsProfileError("Получен слишком маленький ответ от GFS Filter")

    part_path.replace(out_path)
    return out_path


def extract_profile_from_grib_file(grib_path: Path) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp_dir:
        idx_path = os.path.join(tmp_dir, "profile.idx")
        try:
            import cfgrib

            ds = cfgrib.open_dataset(
                str(grib_path),
                backend_kwargs={
                    "indexpath": idx_path,
                    "errors": "raise",
                    "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
                },
            )
        except Exception as exc:
            raise GfsProfileError(f"Ошибка чтения GRIB2: {exc}") from exc

        required = ["t", "r", "u", "v", "gh", "isobaricInhPa"]
        for name in required:
            if name not in ds and name not in ds.coords:
                raise GfsProfileError(f"В GRIB отсутствует поле {name}")

        levels = ds["isobaricInhPa"].values.astype(float)
        t = np.squeeze(ds["t"].values).astype(float)
        rh = np.squeeze(ds["r"].values).astype(float)
        u = np.squeeze(ds["u"].values).astype(float)
        v = np.squeeze(ds["v"].values).astype(float)
        hgt = np.squeeze(ds["gh"].values).astype(float)

        if t.ndim > 1:
            t = t[..., 0, 0]
            rh = rh[..., 0, 0]
            u = u[..., 0, 0]
            v = v[..., 0, 0]
            hgt = hgt[..., 0, 0]

        df = pd.DataFrame(
            {
                "pressure_hpa": levels,
                "temperature_k": t,
                "relative_humidity_pct": rh,
                "u_wind_ms": u,
                "v_wind_ms": v,
                "geopotential_height_m": hgt,
            }
        )
        df = df.dropna(subset=["pressure_hpa", "geopotential_height_m"]).copy()
        return add_derived_parameters(df)


def add_derived_parameters(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["temperature_c"] = out["temperature_k"] - 273.15
    out["wind_speed_ms"] = np.sqrt(out["u_wind_ms"] ** 2 + out["v_wind_ms"] ** 2)
    out["wind_dir_deg"] = (270 - np.degrees(np.arctan2(out["v_wind_ms"], out["u_wind_ms"]))) % 360

    rh_fraction = np.clip(out["relative_humidity_pct"].astype(float), 1.0, 100.0) / 100.0
    temp_c = out["temperature_c"].astype(float)
    alpha = np.log(rh_fraction) + (17.625 * temp_c) / (243.04 + temp_c)
    out["dewpoint_c"] = (243.04 * alpha) / (17.625 - alpha)
    out["theta_k"] = out["temperature_k"] * np.power(1000.0 / out["pressure_hpa"], 0.286)

    return out.sort_values("geopotential_height_m").reset_index(drop=True)


def freezing_level_diagnostic(df: pd.DataFrame) -> dict[str, float | str | None]:
    if df.empty or "temperature_c" not in df or "geopotential_height_m" not in df:
        return {"status": "not_available", "height_m": None}

    prof = df.sort_values("geopotential_height_m")[["temperature_c", "geopotential_height_m"]].dropna()
    if prof.empty:
        return {"status": "not_available", "height_m": None}

    temps = prof["temperature_c"].to_numpy(dtype=float)
    heights = prof["geopotential_height_m"].to_numpy(dtype=float)

    if np.all(temps < 0):
        return {"status": "below_lowest_level", "height_m": None}
    if np.all(temps > 0):
        return {"status": "above_highest_level", "height_m": None}

    for i in range(len(temps) - 1):
        t0, t1 = temps[i], temps[i + 1]
        if math.isclose(t0, 0.0, abs_tol=0.05):
            return {"status": "found", "height_m": float(heights[i])}
        if (t0 >= 0 >= t1) or (t0 <= 0 <= t1):
            if math.isclose(t0, t1, abs_tol=1e-9):
                return {"status": "found", "height_m": float(heights[i])}
            ratio = (0 - t0) / (t1 - t0)
            height = heights[i] + ratio * (heights[i + 1] - heights[i])
            return {"status": "found", "height_m": float(height)}

    return {"status": "not_available", "height_m": None}


def freezing_level_m(df: pd.DataFrame) -> float | None:
    diagnostic = freezing_level_diagnostic(df)
    return float(diagnostic["height_m"]) if diagnostic["status"] == "found" and diagnostic["height_m"] is not None else None


def build_profile(run: GfsRun, lead_hour: int, lat: float, lon: float) -> ProfileResult:
    validate_lead(lead_hour)
    if not forecast_file_exists(run.date, run.cycle, lead_hour):
        raise GfsProfileError(f"Для указанной даты/цикла/срока данные GFS недоступны: {run.date} {run.cycle}Z +{lead_hour} ч")

    grid_lat, grid_lon = snap_to_gfs_grid(lat, lon)
    grib_path = download_profile_grib_to_disk(run.date, run.cycle, lead_hour, grid_lat, grid_lon)
    df = extract_profile_from_grib_file(grib_path)
    return ProfileResult(
        run=run,
        lead_hour=lead_hour,
        requested_lat=lat,
        requested_lon=lon,
        grid_lat=grid_lat,
        grid_lon=grid_lon,
        grib_path=grib_path,
        dataframe=df,
    )


def format_cli_summary(result: ProfileResult) -> str:
    lines = [
        "GFS 0.25: модельный профиль атмосферы",
        f"Запуск: {result.run.date}/{result.run.cycle} | срок: +{result.lead_hour} ч | действительно на: {result.valid_time_utc:%Y-%m-%d %H:%M UTC}",
        f"Запрошено: {result.requested_lat:.4f},{result.requested_lon:.4f} | узел GFS: {result.grid_lat:.3f},{result.grid_lon:.3f}",
    ]
    df = result.dataframe
    for level in (1000, 925, 850, 700, 500, 300):
        if df.empty:
            continue
        idx = (df["pressure_hpa"] - level).abs().idxmin()
        row = df.loc[idx]
        if abs(float(row["pressure_hpa"]) - level) > 35:
            continue
        lines.append(
            f"{int(round(row['pressure_hpa'])):4d} гПа "
            f"z={int(round(row['geopotential_height_m'])):5d} м "
            f"T={row['temperature_c']:+5.1f} °C RH={row['relative_humidity_pct']:5.1f}% "
            f"ветер={row['wind_dir_deg']:03.0f}°/{row['wind_speed_ms']:.1f} м/с"
        )
    diagnostic = freezing_level_diagnostic(df)
    if diagnostic["status"] == "found":
        lines.append(f"0 °C: {float(diagnostic['height_m']):.0f} м")
    return "\n".join(lines)


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Загрузить и напечатать точечный вертикальный профиль GFS 0.25.")
    parser.add_argument("--lat", type=float, required=True, help="Широта")
    parser.add_argument("--lon", type=float, required=True, help="Долгота")
    parser.add_argument("--lead", type=int, default=24, help="Срок прогноза, часы")
    parser.add_argument("--date", help="Дата запуска GFS YYYYMMDD. Если не задано, берётся последний доступный запуск для указанного срока.")
    parser.add_argument("--cycle", choices=("00", "06", "12", "18"), help="Цикл GFS. Обязателен вместе с --date.")
    parser.add_argument("--json", action="store_true", help="Напечатать полный JSON вместо компактной сводки.")
    parser.add_argument("--csv", type=Path, help="Путь для записи полного CSV-профиля.")
    args = parser.parse_args(argv)

    try:
        if args.date and not args.cycle:
            raise GfsProfileError("Если задан --date, нужно задать --cycle")
        run = GfsRun(args.date, args.cycle) if args.date else latest_available_run_for_lead(args.lead)
        result = build_profile(run, args.lead, args.lat, args.lon)
        if args.csv:
            result.dataframe.round(3).to_csv(args.csv, index=False)
        if args.json:
            print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
        else:
            print(format_cli_summary(result))
            if args.csv:
                print(f"CSV: {args.csv}")
        return 0
    except GfsProfileError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
