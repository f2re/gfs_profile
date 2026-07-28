from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from gfs_core import CACHE_DIR, GfsProfileError, GfsRun, build_profile, canonical_leads, cycle_exists, forecast_file_exists

app = FastAPI(title="Профиль атмосферы GFS")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _now_ts() -> float:
    return time.time()


def _set_job(job_id: str, **kwargs: Any) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {"status": "queued", "progress": 0})
        JOBS[job_id].update(kwargs)


def _http_error(exc: GfsProfileError) -> HTTPException:
    message = str(exc)
    if "недоступ" in message or "Не найден" in message or "не опубликован" in message:
        return HTTPException(status_code=404, detail=message)
    if "lead_hour" in message or "диапазон" in message:
        return HTTPException(status_code=400, detail=message)
    if "NOMADS" in message or "загруз" in message or "GRIB" in message:
        return HTTPException(status_code=502, detail=message)
    return HTTPException(status_code=500, detail=message)


def _build_profile_payload(date: str, cycle: str, lead_index: int, lat: float, lon: float, job_id: str | None = None) -> dict[str, Any]:
    try:
        if job_id:
            _set_job(job_id, stage="download", progress=5)
        result = build_profile(GfsRun(date=date, cycle=cycle), lead_index, lat, lon)
        if job_id:
            _set_job(job_id, stage="parse", progress=95)
        return result.to_payload()
    except GfsProfileError as exc:
        raise _http_error(exc) from exc


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
        if cycle_exists(date, cycle):
            cycles.append({"cycle": cycle, "forecast_steps": len(canonical_leads())})
    return {"date": date, "cycles": cycles}


@app.get("/api/available-leads")
def available_leads(date: str = Query(..., pattern=r"^\d{8}$"), cycle: str = Query(..., pattern=r"^(00|06|12|18)$")) -> dict[str, Any]:
    if not cycle_exists(date, cycle):
        raise HTTPException(status_code=404, detail="Для указанной даты/цикла данные GFS недоступны")
    run_dt = datetime.strptime(f"{date}{cycle}", "%Y%m%d%H")
    leads = []
    for fh in canonical_leads():
        if not forecast_file_exists(date, cycle, fh):
            continue
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
    worker = threading.Thread(target=_profile_job_worker, args=(job_id, date, cycle, lead_index, lat, lon), daemon=True)
    worker.start()
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
    forecast_cache = forecast_file_exists.cache_info()
    cycle_cache = cycle_exists.cache_info()
    cached_files = list(CACHE_DIR.glob("*.grib2"))
    return {
        "grib_cache": {"hits": 0, "misses": 0, "maxsize": 0, "currsize": len(cached_files)},
        "forecast_file_cache": {
            "hits": forecast_cache.hits,
            "misses": forecast_cache.misses,
            "maxsize": forecast_cache.maxsize,
            "currsize": forecast_cache.currsize,
        },
        "cycle_cache": {
            "hits": cycle_cache.hits,
            "misses": cycle_cache.misses,
            "maxsize": cycle_cache.maxsize,
            "currsize": cycle_cache.currsize,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
