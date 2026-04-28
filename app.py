from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

app = FastAPI(title="Профиль атмосферы GFS")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def dataset_url(date: str, cycle: str) -> str:
    return f"https://nomads.ncep.noaa.gov/dods/gfs_0p25/gfs{date}/gfs_0p25_{cycle}z"


@lru_cache(maxsize=16)
def open_gfs_dataset(date: str, cycle: str) -> xr.Dataset:
    url = dataset_url(date, cycle)
    try:
        # OPeNDAP поток открывается лениво; фактическое чтение идет при выборке данных.
        return xr.open_dataset(url, engine="netcdf4")
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Не удалось открыть датасет: {url}") from exc


def nearest_lon(ds: xr.Dataset, lon: float) -> float:
    lon_values = ds["lon"].values
    norm_lon = lon % 360
    idx = int(np.abs(lon_values - norm_lon).argmin())
    return float(lon_values[idx])


def extract_profile(ds: xr.Dataset, time_index: int, lat: float, lon: float) -> pd.DataFrame:
    nearest = {
        "lat": float(ds["lat"].sel(lat=lat, method="nearest").values),
        "lon": nearest_lon(ds, lon),
    }

    coords = {"time": time_index, "lat": nearest["lat"], "lon": nearest["lon"]}
    required_vars = {
        "temperature_k": "tmpprs",
        "relative_humidity_pct": "rhprs",
        "u_wind_ms": "ugrdprs",
        "v_wind_ms": "vgrdprs",
        "geopotential_height_m": "hgtprs",
    }

    data: dict[str, Any] = {}
    for out_name, ds_name in required_vars.items():
        if ds_name not in ds:
            raise HTTPException(status_code=500, detail=f"Переменная {ds_name} не найдена в датасете")
        data[out_name] = ds[ds_name].sel(**coords, method="nearest").values.astype(float)

    pressure_hpa = ds["lev"].values.astype(float)

    df = pd.DataFrame({"pressure_hpa": pressure_hpa, **data})
    df = df.dropna(subset=["geopotential_height_m"])

    df["temperature_c"] = df["temperature_k"] - 273.15
    df["wind_speed_ms"] = np.sqrt(df["u_wind_ms"] ** 2 + df["v_wind_ms"] ** 2)
    # направление, ОТКУДА дует, в градусах, метеорологический стандарт
    df["wind_dir_deg"] = (270 - np.degrees(np.arctan2(df["v_wind_ms"], df["u_wind_ms"]))) % 360

    df = df.sort_values("geopotential_height_m").reset_index(drop=True)
    return df


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/available-cycles")
def available_cycles(date: str = Query(..., pattern=r"^\d{8}$")) -> dict[str, Any]:
    cycles = []
    for cycle in ["00", "06", "12", "18"]:
        try:
            ds = open_gfs_dataset(date, cycle)
            n_steps = int(ds.sizes.get("time", 0))
            if n_steps > 0:
                cycles.append({"cycle": cycle, "forecast_steps": n_steps})
        except HTTPException:
            continue
    return {"date": date, "cycles": cycles}


@app.get("/api/available-leads")
def available_leads(date: str = Query(..., pattern=r"^\d{8}$"), cycle: str = Query(..., pattern=r"^(00|06|12|18)$")) -> dict[str, Any]:
    ds = open_gfs_dataset(date, cycle)
    times = pd.to_datetime(ds["time"].values)
    run_dt = datetime.strptime(f"{date}{cycle}", "%Y%m%d%H")

    leads = []
    for idx, valid_time in enumerate(times):
        lead_hours = int((valid_time.to_pydatetime() - run_dt).total_seconds() // 3600)
        leads.append(
            {
                "index": idx,
                "lead_hours": lead_hours,
                "valid_time_utc": valid_time.strftime("%Y-%m-%d %H:%M"),
            }
        )
    return {"date": date, "cycle": cycle, "leads": leads}


@app.get("/api/profile")
def profile(
    date: str = Query(..., pattern=r"^\d{8}$"),
    cycle: str = Query(..., pattern=r"^(00|06|12|18)$"),
    lead_index: int = Query(..., ge=0),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    ds = open_gfs_dataset(date, cycle)
    if lead_index >= ds.sizes.get("time", 0):
        raise HTTPException(status_code=400, detail="lead_index вне диапазона")

    valid_time = pd.to_datetime(ds["time"].values[lead_index]).strftime("%Y-%m-%d %H:%M")
    nearest_lat = float(ds["lat"].sel(lat=lat, method="nearest").values)
    nearest_lon_point = nearest_lon(ds, lon)
    nearest_lon_wgs84 = nearest_lon_point if nearest_lon_point <= 180 else nearest_lon_point - 360

    df = extract_profile(ds, lead_index, lat, lon)

    max_h = float(df["geopotential_height_m"].max()) if not df.empty else 0.0
    return {
        "meta": {
            "date": date,
            "cycle": cycle,
            "lead_index": lead_index,
            "valid_time_utc": valid_time,
            "requested_point": {"lat": lat, "lon": lon},
            "nearest_grid_point": {"lat": nearest_lat, "lon": nearest_lon_wgs84},
            "max_height_m": max_h,
        },
        "columns": list(df.columns),
        "rows": df.round(3).to_dict(orient="records"),
    }


@app.get("/api/cache-info")
def cache_info() -> dict[str, Any]:
    info = open_gfs_dataset.cache_info()
    return {
        "hits": info.hits,
        "misses": info.misses,
        "maxsize": info.maxsize,
        "currsize": info.currsize,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
