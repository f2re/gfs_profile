from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
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
CACHE_TTL_SECONDS = 24 * 3600
CACHE_DIR = Path(".cache_gfs")
CACHE_DIR.mkdir(exist_ok=True)

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _now_ts() -> float:
    return time.time()


def _set_job(job_id: str, **kwargs: Any) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {"status": "queued", "progress": 0})
        JOBS[job_id].update(kwargs)


def _clean_old_cache() -> None:
    cutoff = _now_ts() - CACHE_TTL_SECONDS
    for p in CACHE_DIR.glob("*.grib2"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        except OSError:
            continue


def _run_file_name(cycle: str, lead_hour: int) -> str:
    return f"gfs.t{cycle}z.pgrb2.0p25.f{lead_hour:03d}"


def _run_dir(date: str, cycle: str) -> str:
    return f"/gfs.{date}/{cycle}/atmos"


def _cache_key(date: str, cycle: str, lead_hour: int, lat: float, lon: float) -> str:
    return f"{date}_{cycle}_f{lead_hour:03d}_{lat:.3f}_{lon:.3f}".replace("-", "m")


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
        "dir": _run_dir(date, cycle),
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


def _download_profile_grib_to_disk(date: str, cycle: str, lead_hour: int, lat: float, lon: float, job_id: str | None = None) -> Path:
    _clean_old_cache()
    key = _cache_key(date, cycle, lead_hour, lat, lon)
    out_path = CACHE_DIR / f"{key}.grib2"
    if out_path.exists():
        if job_id:
            _set_job(job_id, progress=100, stage="cache_hit")
        return out_path

    url = _grib_filter_url(date, cycle, lead_hour, lat, lon)
    part_path = CACHE_DIR / f"{key}.part"

    if job_id:
        _set_job(job_id, stage="download", progress=1)

    try:
        with requests.get(url, timeout=TIMEOUT, stream=True) as r:
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Ошибка загрузки GFS ({r.status_code})")

            ct = r.headers.get("content-type", "").lower()
            if "text/html" in ct:
                raise HTTPException(status_code=502, detail="NOMADS вернул HTML вместо GRIB2")

            total = int(r.headers.get("content-length", "0"))
            downloaded = 0
            with open(part_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if job_id:
                        if total > 0:
                            progress = min(90, int(downloaded * 90 / total))
                        else:
                            progress = min(90, 1 + downloaded // 50000)
                        _set_job(job_id, progress=progress, downloaded_bytes=downloaded, total_bytes=total)
    except HTTPException:
        part_path.unlink(missing_ok=True)
        raise
    except RequestException as exc:
        part_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"Ошибка подключения к NOMADS: {exc}") from exc

    if not part_path.exists() or part_path.stat().st_size < 256:
        part_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail="Получен слишком маленький ответ от GFS фильтра")

    part_path.replace(out_path)
    if job_id:
        _set_job(job_id, progress=92, stage="download_complete")
    return out_path


def _extract_profile_from_grib_file(grib_path: Path) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as td:
        idx_path = os.path.join(td, "profile.idx")
        try:
            ds = cfgrib.open_dataset(
                str(grib_path),
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


def _build_profile_payload(date: str, cycle: str, lead_index: int, lat: float, lon: float, job_id: str | None = None) -> dict[str, Any]:
    if not _cycle_exists(date, cycle):
        raise HTTPException(status_code=404, detail="Для указанной даты/цикла данные GFS недоступны")
    if lead_index not in _canonical_leads():
        raise HTTPException(status_code=400, detail="lead_index вне допустимого диапазона GFS")

    if job_id:
        _set_job(job_id, stage="download", progress=1)
    grib_path = _download_profile_grib_to_disk(date, cycle, lead_index, lat, lon, job_id=job_id)

    if job_id:
        _set_job(job_id, stage="parse", progress=95)
    df = _extract_profile_from_grib_file(grib_path)

    run_dt = datetime.strptime(f"{date}{cycle}", "%Y%m%d%H")
    valid_time = (run_dt + timedelta(hours=lead_index)).strftime("%Y-%m-%d %H:%M")

    return {
        "meta": {
            "date": date,
            "cycle": cycle,
            "lead_index": lead_index,
            "valid_time_utc": valid_time,
            "requested_point": {"lat": lat, "lon": lon},
            "max_height_m": float(df["geopotential_height_m"].max()) if not df.empty else 0.0,
            "source": "NOMADS GRIB Filter + disk cache",
            "rows": int(len(df)),
            "cache_file": str(grib_path.name),
        },
        "columns": list(df.columns),
        "rows": df.round(3).to_dict(orient="records"),
    }


def _profile_job_worker(job_id: str, date: str, cycle: str, lead_index: int, lat: float, lon: float) -> None:
    try:
        payload = _build_profile_payload(date, cycle, lead_index, lat, lon, job_id=job_id)
        _set_job(job_id, status="done", progress=100, stage="done", result=payload)
    except HTTPException as exc:
        _set_job(job_id, status="error", stage="error", error=exc.detail)
    except Exception as exc:
        _set_job(job_id, status="error", stage="error", error=f"Непредвиденная ошибка: {exc}")


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
    return _build_profile_payload(date, cycle, lead_index, lat, lon)


@app.post("/api/profile/start")
def profile_start(
    date: str = Query(..., pattern=r"^\d{8}$"),
    cycle: str = Query(..., pattern=r"^(00|06|12|18)$"),
    lead_index: int = Query(..., ge=0),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    _set_job(job_id, status="running", stage="queued", progress=0, created_at=_now_ts())
    t = threading.Thread(target=_profile_job_worker, args=(job_id, date, cycle, lead_index, lat, lon), daemon=True)
    t.start()
    return {"job_id": job_id}


@app.get("/api/profile/status")
def profile_status(job_id: str = Query(...)) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id не найден")
    return job


@app.get("/api/cache-info")
def cache_info() -> dict[str, Any]:
    c = _cycle_exists.cache_info()
    cached_files = list(CACHE_DIR.glob("*.grib2"))
    return {
        "grib_cache": {"hits": 0, "misses": 0, "maxsize": 0, "currsize": len(cached_files)},
        "lead_cache": {"hits": c.hits, "misses": c.misses, "maxsize": c.maxsize, "currsize": c.currsize},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
