from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import cfgrib
import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from requests import RequestException
from starlette.requests import Request

app = FastAPI(title="Профиль атмосферы GFS")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

NOMADS_BASE = "https://nomads.ncep.noaa.gov"
TIMEOUT = 35


def _run_file_name(cycle: str, lead_hour: int) -> str:
    return f"gfs.t{cycle}z.pgrb2.0p25.f{lead_hour:03d}"


def _run_dir(date: str, cycle: str) -> str:
    return f"/gfs.{date}/{cycle}/atmos"


def _grib_filter_url(date: str, cycle: str, lead_hour: int, lat: float, lon: float) -> str:
    lon_360 = lon % 360
    file_name = _run_file_name(cycle, lead_hour)
    run_dir = _run_dir(date, cycle)
    q = {
        "file": file_name,
        "all_lev": "on",
        "var_TMP": "on",
        "var_RH": "on",
        "var_UGRD": "on",
        "var_VGRD": "on",
        "var_HGT": "on",
        "leftlon": f"{lon_360:.3f}",
        "rightlon": f"{lon_360 + 0.001:.3f}",
        "toplat": f"{lat + 0.001:.3f}",
        "bottomlat": f"{lat:.3f}",
        "dir": run_dir,
    }
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return f"{NOMADS_BASE}/cgi-bin/filter_gfs_0p25_1hr.pl?{qs}"


def _source_idx_url(date: str, cycle: str, lead_hour: int) -> str:
    file_name = _run_file_name(cycle, lead_hour)
    return f"{NOMADS_BASE}/pub/data/nccf/com/gfs/prod/gfs.{date}/{cycle}/atmos/{file_name}.idx"


def _parse_pressure_level(ds_list: list[Any], var_name: str) -> tuple[np.ndarray, np.ndarray]:
    for ds in ds_list:
        if "isobaricInhPa" not in ds.coords or var_name not in ds.data_vars:
            continue
        levels = ds["isobaricInhPa"].values.astype(float)
        vals = np.squeeze(ds[var_name].values).astype(float)
        if vals.ndim == 3:
            vals = vals[:, 0, 0]
        elif vals.ndim == 2:
            vals = vals[:, 0]
        return levels, vals
    raise HTTPException(status_code=500, detail=f"Не найдена переменная {var_name} на изобарических уровнях")


@lru_cache(maxsize=128)
def _cycle_exists(date: str, cycle: str) -> bool:
    try:
        r = requests.head(_source_idx_url(date, cycle, 0), timeout=12)
        return r.status_code == 200
    except RequestException:
        return False


@lru_cache(maxsize=256)
def _download_profile_grib(date: str, cycle: str, lead_hour: int, lat: float, lon: float) -> bytes:
    url = _grib_filter_url(date, cycle, lead_hour, lat, lon)
    try:
        r = requests.get(url, timeout=TIMEOUT)
    except RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка подключения к NOMADS: {exc}") from exc

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Ошибка загрузки GFS ({r.status_code})")

    ct = r.headers.get("content-type", "").lower()
    if "text/html" in ct or r.text.lstrip().lower().startswith("<!doctype"):
        raise HTTPException(status_code=502, detail="NOMADS вернул HTML вместо GRIB2 (проверьте путь/доступность run).")

    if len(r.content) < 256:
        raise HTTPException(status_code=502, detail="Получен слишком маленький ответ от GFS фильтра")

    return r.content


def _extract_profile_from_grib(grib_bytes: bytes) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as td:
        data_path = os.path.join(td, "profile.grib2")
        idx_path = os.path.join(td, "profile.idx")
        with open(data_path, "wb") as f:
            f.write(grib_bytes)

        try:
            ds_list = cfgrib.open_datasets(data_path, backend_kwargs={"indexpath": idx_path, "errors": "raise"})
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка чтения GRIB2: {exc}") from exc

        levels_t, t = _parse_pressure_level(ds_list, "t")
        levels_r, rh = _parse_pressure_level(ds_list, "r")
        levels_u, u = _parse_pressure_level(ds_list, "u")
        levels_v, v = _parse_pressure_level(ds_list, "v")
        levels_h, hgt = _parse_pressure_level(ds_list, "gh")

        base = levels_t
        for lv in [levels_r, levels_u, levels_v, levels_h]:
            if len(lv) != len(base) or not np.allclose(lv, base):
                raise HTTPException(status_code=500, detail="Несовпадение вертикальных уровней между переменными")

        df = pd.DataFrame(
            {
                "pressure_hpa": base,
                "temperature_k": t,
                "relative_humidity_pct": rh,
                "u_wind_ms": u,
                "v_wind_ms": v,
                "geopotential_height_m": hgt,
            }
        )
        df = df.dropna(subset=["geopotential_height_m"]).copy()
        df["temperature_c"] = df["temperature_k"] - 273.15
        df["wind_speed_ms"] = np.sqrt(df["u_wind_ms"] ** 2 + df["v_wind_ms"] ** 2)
        df["wind_dir_deg"] = (270 - np.degrees(np.arctan2(df["v_wind_ms"], df["u_wind_ms"]))) % 360
        return df.sort_values("geopotential_height_m").reset_index(drop=True)


def _canonical_leads() -> list[int]:
    return list(range(0, 121)) + list(range(123, 385, 3))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/available-cycles")
def available_cycles(date: str = Query(..., pattern=r"^\d{8}$")) -> dict[str, Any]:
    cycles = []
    for cycle in ["00", "06", "12", "18"]:
        if _cycle_exists(date, cycle):
            cycles.append({"cycle": cycle, "forecast_steps": len(_canonical_leads())})
    return {"date": date, "cycles": cycles}


@app.get("/api/available-leads")
def available_leads(date: str = Query(..., pattern=r"^\d{8}$"), cycle: str = Query(..., pattern=r"^(00|06|12|18)$")) -> dict[str, Any]:
    if not _cycle_exists(date, cycle):
        raise HTTPException(status_code=404, detail="Для указанной даты/цикла данные GFS недоступны")

    run_dt = datetime.strptime(f"{date}{cycle}", "%Y%m%d%H")
    leads = []
    for fh in _canonical_leads():
        valid_time = run_dt + timedelta(hours=fh)
        leads.append({"index": fh, "lead_hours": fh, "valid_time_utc": valid_time.strftime("%Y-%m-%d %H:%M")})
    return {"date": date, "cycle": cycle, "leads": leads}


@app.get("/api/profile")
def profile(
    date: str = Query(..., pattern=r"^\d{8}$"),
    cycle: str = Query(..., pattern=r"^(00|06|12|18)$"),
    lead_index: int = Query(..., ge=0),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    if not _cycle_exists(date, cycle):
        raise HTTPException(status_code=404, detail="Для указанной даты/цикла данные GFS недоступны")

    if lead_index not in _canonical_leads():
        raise HTTPException(status_code=400, detail="lead_index вне допустимого диапазона GFS")

    grib = _download_profile_grib(date, cycle, lead_index, lat, lon)
    df = _extract_profile_from_grib(grib)

    run_dt = datetime.strptime(f"{date}{cycle}", "%Y%m%d%H")
    valid_time = (run_dt + timedelta(hours=lead_index)).strftime("%Y-%m-%d %H:%M")

    max_h = float(df["geopotential_height_m"].max()) if not df.empty else 0.0
    return {
        "meta": {
            "date": date,
            "cycle": cycle,
            "lead_index": lead_index,
            "valid_time_utc": valid_time,
            "requested_point": {"lat": lat, "lon": lon},
            "max_height_m": max_h,
            "source": "NOMADS GRIB Filter",
        },
        "columns": list(df.columns),
        "rows": df.round(3).to_dict(orient="records"),
    }


@app.get("/api/cache-info")
def cache_info() -> dict[str, Any]:
    g = _download_profile_grib.cache_info()
    c = _cycle_exists.cache_info()
    return {
        "grib_cache": {"hits": g.hits, "misses": g.misses, "maxsize": g.maxsize, "currsize": g.currsize},
        "lead_cache": {"hits": c.hits, "misses": c.misses, "maxsize": c.maxsize, "currsize": c.currsize},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
