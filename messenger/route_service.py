from __future__ import annotations

"""Messenger-neutral GFS route profile service."""

import re
from typing import Any, Callable, NamedTuple

from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead, validate_lead
from route_profile import ROUTE_DEFAULT_SPEED_KMH, ROUTE_MAX_SPEED_KMH, ROUTE_MIN_SPEED_KMH, write_route_csv
from route_profile_contract import (
    ROUTE_SPATIAL_STEP_KM,
    ROUTE_SPATIAL_STEPS_KM,
    build_route_profile_data,
    route_summary,
    route_waypoint_specs,
    validate_spatial_step,
)
from route_profile_plot import write_route_profile_png

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
SPEED_RE = re.compile(r"\b(?:speed|v|скорость)=(?P<value>\d{2,4})\b", re.IGNORECASE)
MODE_RE = re.compile(r"\bmode=(?P<value>simple|pro|простой|профи)\b", re.IGNORECASE)
STEP_RE = re.compile(r"\b(?:step|grid|шаг)=(?P<value>25|50|100)(?:\s*км)?\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:\blead=|\+)(?P<value>\d{1,3})(?:\s*(?:h|ч))?\b", re.IGNORECASE)
ROUTE_SPLIT_RE = re.compile(r"\s*(?:->|→|=>|\|)\s*")
ROUTE_LEADS = (0, 6, 12, 24, 48)
ROUTE_SPEEDS = (150, 300, 450, 600)
ROUTE_MODES = ("simple", "pro")
DEFAULT_ROUTE_PARAMS = {
    "lead": 24,
    "speed": int(ROUTE_DEFAULT_SPEED_KMH),
    "mode": "simple",
    "spatial_step": int(ROUTE_SPATIAL_STEP_KM),
}


class ParsedRouteInput(NamedTuple):
    origin_query: str
    destination_query: str
    departure_lead: int
    speed_kmh: int
    mode: str
    run: GfsRun | None
    spatial_step_km: int
    step_explicit: bool


def normalize_route_mode(value: Any) -> str:
    raw = str(value or "simple").strip().lower()
    if raw in {"simple", "простой"}:
        return "simple"
    if raw in {"pro", "профи"}:
        return "pro"
    raise GfsProfileError("mode должен быть simple или pro")


def normalize_route_params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_ROUTE_PARAMS)
    if value:
        aliases = {"departure_lead": "lead", "speed_kmh": "speed", "spatial_step_km": "spatial_step"}
        for key, item in value.items():
            target = aliases.get(key, key)
            if target in result:
                result[target] = item
    result["lead"] = int(result["lead"])
    result["speed"] = int(result["speed"])
    result["mode"] = normalize_route_mode(result["mode"])
    result["spatial_step"] = validate_spatial_step(int(result["spatial_step"]))
    validate_lead(result["lead"])
    if not ROUTE_MIN_SPEED_KMH <= result["speed"] <= ROUTE_MAX_SPEED_KMH:
        raise GfsProfileError(f"Скорость должна быть {ROUTE_MIN_SPEED_KMH}…{ROUTE_MAX_SPEED_KMH} км/ч")
    return result


def parse_route_input(raw_text: str, default_lead: int = 24) -> ParsedRouteInput:
    text = str(raw_text or "").strip()
    run = None
    match = RUN_RE.search(text)
    if match:
        run = GfsRun(match.group("date"), match.group("cycle"))
        text = (text[:match.start()] + text[match.end():]).strip()

    params = {**DEFAULT_ROUTE_PARAMS, "lead": int(default_lead)}
    match = SPEED_RE.search(text)
    if match:
        params["speed"] = int(match.group("value")); text = (text[:match.start()] + text[match.end():]).strip()
    match = MODE_RE.search(text)
    if match:
        params["mode"] = match.group("value"); text = (text[:match.start()] + text[match.end():]).strip()
    step_explicit = False
    match = STEP_RE.search(text)
    if match:
        params["spatial_step"] = int(match.group("value")); step_explicit = True
        text = (text[:match.start()] + text[match.end():]).strip()
    match = LEAD_RE.search(text)
    if match:
        params["lead"] = int(match.group("value")); text = (text[:match.start()] + text[match.end():]).strip()
    params = normalize_route_params(params)

    parts = [part.strip(" ,;") for part in ROUTE_SPLIT_RE.split(text, maxsplit=1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Маршрут задаётся через → или ->. Пример: Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro")
    return ParsedRouteInput(parts[0], parts[1], params["lead"], params["speed"], params["mode"], run, params["spatial_step"], step_explicit)


def _progress_adapter(callback: Callable[[ProgressEvent], None] | None):
    if callback is None:
        return None
    def emit(event: dict[str, Any]) -> None:
        callback(ProgressEvent(
            stage=str(event.get("stage", "progress")),
            message=str(event.get("message", "")),
            current=int(event["index"]) if event.get("index") is not None else None,
            total=int(event["total"]) if event.get("total") is not None else None,
            data=dict(event),
        ))
    return emit


def route_plan(origin: Any, destination: Any, params: dict[str, Any]) -> tuple[float, float, list[Any], int]:
    p = normalize_route_params(params)
    distance, duration, specs = route_waypoint_specs(
        origin, destination, p["lead"], p["speed"], spatial_step_km=p["spatial_step"]
    )
    max_lead = max(int(item[4]) for item in specs)
    return float(distance), float(duration), list(specs), max_lead


def route_recipe_params(origin: Any, destination: Any, params: dict[str, Any]) -> dict[str, Any]:
    p = normalize_route_params(params)
    p["origin"] = {"lat": float(origin.lat), "lon": float(origin.lon), "label": str(origin.label), "source": str(getattr(origin, "source", "route"))}
    p["destination"] = {"lat": float(destination.lat), "lon": float(destination.lon), "label": str(destination.label), "source": str(getattr(destination, "source", "route"))}
    return p


def route_repeat_command(origin: Any, destination: Any, params: dict[str, Any], run: GfsRun) -> str:
    p = normalize_route_params(params)
    return (
        f"/route {float(origin.lat):.4f} {float(origin.lon):.4f} -> {float(destination.lat):.4f} {float(destination.lon):.4f} "
        f"+{p['lead']} speed={p['speed']} step={p['spatial_step']} mode={p['mode']} run={run.date}/{run.cycle}"
    )


def build_route_product_result(
    origin: Any,
    destination: Any,
    departure_lead: int = 24,
    speed_kmh: int = ROUTE_DEFAULT_SPEED_KMH,
    mode: str = "simple",
    spatial_step_km: int = ROUTE_SPATIAL_STEP_KM,
    run: GfsRun | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    run_selector: Callable[[int], GfsRun] | None = None,
) -> CommonProductResult:
    params = normalize_route_params({"lead": departure_lead, "speed": speed_kmh, "mode": mode, "spatial_step": spatial_step_km})
    distance, duration, specs, max_lead = route_plan(origin, destination, params)
    if progress_callback:
        progress_callback(ProgressEvent("check", "Проверяю опубликованный цикл", data={"max_lead": max_lead, "points": len(specs)}))
    selected_run = run or (run_selector or latest_available_run_for_lead)(max_lead)
    if progress_callback:
        progress_callback(ProgressEvent("run", f"GFS {selected_run.date} {selected_run.cycle}Z", data={"run_date": selected_run.date, "run_cycle": selected_run.cycle, "max_lead": max_lead}))
    callback = _progress_adapter(progress_callback)
    png_path = None
    csv_path = None
    try:
        data = build_route_profile_data(
            selected_run,
            origin,
            destination,
            params["lead"],
            speed_kmh=params["speed"],
            mode=params["mode"],
            progress_callback=callback,
            spatial_step_km=params["spatial_step"],
        )
        if progress_callback:
            progress_callback(ProgressEvent("plot_start", "Строю маршрутный PNG/CSV"))
        png_path = write_route_profile_png(data)
        csv_path = write_route_csv(data)
        if progress_callback:
            progress_callback(ProgressEvent("plot_done", "PNG и CSV готовы"))
        metadata = {
            "model": "GFS 0.25", "data_kind": "model", "product": "route",
            "run_date": selected_run.date, "run_cycle": selected_run.cycle,
            "departure_lead": params["lead"], "max_lead": max_lead,
            "speed_kmh": params["speed"], "mode": params["mode"], "spatial_step_km": params["spatial_step"],
            "distance_km": distance, "duration_hours": duration, "point_count": len(specs),
            "origin": route_recipe_params(origin, destination, params)["origin"],
            "destination": route_recipe_params(origin, destination, params)["destination"],
        }
        return CommonProductResult(
            product="route",
            summary=route_summary(data),
            attachments=[
                ProductAttachment("image", png_path, png_path.name, f"PNG · ROUTE {params['mode'].upper()} · GFS {selected_run.date} {selected_run.cycle}Z · {origin.label} → {destination.label}", "image/png"),
                ProductAttachment("file", csv_path, csv_path.name, "CSV · точки маршрута, ETA, уровни до 500 гПа и модельные диагностические риски", "text/csv"),
            ],
            metadata=metadata,
            repeat_command=route_repeat_command(origin, destination, params, selected_run),
        )
    except Exception:
        if png_path: png_path.unlink(missing_ok=True)
        if csv_path: csv_path.unlink(missing_ok=True)
        raise
