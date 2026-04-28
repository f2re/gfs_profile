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
MAX_GRIB_BYTES = 6 * 1024 * 1024  # защита от избыточного ответа


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


@lru_cache(maxsize=128)
def _cycle_exists(date: str, cycle: str) -> bool:
    try:
        r = requests.head(_source_idx_url(date, cycle, 0), timeout=12)
        return r.status_code == 200
    except RequestException:
        return False


def _download_profile_grib(date: str, cycle: str, lead_hour: int, lat: float, lon: float) -> bytes:
    url = _grib_filter_url(date, cycle, lead_hour, lat, lon)
    try:
        with requests.get(url, timeout=TIMEOUT, stream=True) as r:
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Ошибка загрузки GFS ({r.status_code})")

            content_type = r.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                body = r.text[:500].lower()
                if "opendap" in body or "retired" in body:
                    raise HTTPException(status_code=502, detail="NOMADS вернул HTML-ошибку (путь данных недоступен).")
                raise HTTPException(status_code=502, detail="NOMADS вернул HTML вместо GRIB2.")

            chunks: list[bytes] = []
            total = 0
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_GRIB_BYTES:
                    raise HTTPException(status_code=502, detail="Ответ GRIB слишком большой для точечного запроса")
                chunks.append(chunk)
            data = b"".join(chunks)
    except HTTPException:
        raise
    except RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка подключения к NOMADS: {exc}") from exc

    if len(data) < 256:
        raise HTTPException(status_code=502, detail="Получен слишком маленький ответ от GFS фильтра")
    return data


def _extract_profile_from_grib(grib_bytes: bytes) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as td:
        data_path = os.path.join(td, "profile.grib2")
        idx_path = os.path.join(td, "profile.idx")
        with open(data_path, "wb") as f:
            f.write(grib_bytes)

        try:
            ds = cfgrib.open_dataset(
                data_path,
                backend_kwargs={
                    "indexpath": idx_path,
                    "errors": "raise",
                    "filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка чтения GRIB2: {exc}") from exc

        required = ["t", "r", "u", "v", "gh", "isobaricInhPa"]
        for v in required:
            if v not in ds and v not in ds.coords:
                raise HTTPException(status_code=500, detail=f"В GRIB отсутствует поле {v}")

        levels = ds["isobaricInhPa"].values.astype(float)
        t = np.squeeze(ds["t"].values).astype(float)
        rh = np.squeeze(ds["r"].values).astype(float)
        u = np.squeeze(ds["u"].values).astype(float)
        v = np.squeeze(ds["v"].values).astype(float)
        hgt = np.squeeze(ds["gh"].values).astype(float)

        # иногда остается лишнее измерение lat/lon=1
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
            "rows": int(len(df)),
        },
        "columns": list(df.columns),
        "rows": df.round(3).to_dict(orient="records"),
    }


@app.get("/api/cache-info")
def cache_info() -> dict[str, Any]:
    c = _cycle_exists.cache_info()
    return {
        "grib_cache": {"hits": 0, "misses": 0, "maxsize": 0, "currsize": 0},
        "lead_cache": {"hits": c.hits, "misses": c.misses, "maxsize": c.maxsize, "currsize": c.currsize},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
