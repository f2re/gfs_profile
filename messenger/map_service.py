from __future__ import annotations

"""Messenger-neutral GFS composite-map service."""

import re
from pathlib import Path
from typing import Any, Callable, NamedTuple

from composite_map import (
    MAP_BASEMAP_DEFAULT,
    MAP_BASEMAPS,
    MAP_MAX_ANIMATION_FRAMES,
    MAP_MAX_PNG_SERIES_FRAMES,
    MAP_RADIUS_KM,
    build_composite_map,
    build_composite_map_frames,
    write_composite_map_gif,
    write_composite_map_png,
)
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead, validate_lead
from map_animation import write_composite_map_mp4

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,3})\b", re.IGNORECASE)
MODE_RE = re.compile(r"\bmode=(?P<value>single|one|png|series|png-series|gif|anim|animation)\b", re.IGNORECASE)
BASEMAP_RE = re.compile(r"\b(?:basemap|base|подложка)=(?P<value>basic|water|places|roads|база|вода|города|дороги)\b", re.IGNORECASE)
RADIUS_RE = re.compile(r"\bradius=(?P<value>\d{1,3})\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:^|\s)(?:lead=|\+|f)(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?(?=\s|$)", re.IGNORECASE)

DEFAULT_MAP_PARAMS = {
    "from": 0,
    "to": 48,
    "step": 3,
    "mode": "gif",
    "radius": int(MAP_RADIUS_KM),
    "basemap": "places" if "places" in MAP_BASEMAPS else MAP_BASEMAP_DEFAULT,
}
MAP_TO_HOURS = (24, 48, 72, 96)
MAP_STEPS = (3, 6, 12)
MAP_MODES = ("gif", "single", "series")


class ParsedMapInput(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    mode: str
    radius_km: float
    basemap: str


def normalize_map_mode(value: str | None) -> str:
    raw = str(value or "gif").strip().lower()
    if raw in {"gif", "anim", "animation"}:
        return "gif"
    if raw in {"single", "one"}:
        return "single"
    if raw in {"series", "png", "png-series"}:
        return "series"
    raise GfsProfileError("mode должен быть gif, single или series")


def normalize_basemap(value: str | None) -> str:
    aliases = {"база": "basic", "вода": "water", "города": "places", "дороги": "roads"}
    raw = aliases.get(str(value or MAP_BASEMAP_DEFAULT).strip().lower(), str(value or MAP_BASEMAP_DEFAULT).strip().lower())
    if raw not in MAP_BASEMAPS:
        raise GfsProfileError("basemap должен быть basic, water, places или roads")
    return raw


def map_leads(lead_from: int, lead_to: int, step: int, mode: str) -> list[int]:
    mode = normalize_map_mode(mode)
    lead_from, lead_to, step = int(lead_from), int(lead_to), int(step)
    if mode == "single":
        lead_to = lead_from
    if lead_from < 0 or lead_to < lead_from:
        raise GfsProfileError("Некорректный диапазон сроков карты")
    if step <= 0:
        raise GfsProfileError("Шаг карты должен быть положительным")
    leads = list(range(lead_from, lead_to + 1, step))
    if not leads or leads[-1] != lead_to:
        leads.append(lead_to)
    for lead in leads:
        validate_lead(lead)
    if mode == "gif" and len(leads) > MAP_MAX_ANIMATION_FRAMES:
        raise GfsProfileError(f"Анимация ограничена {MAP_MAX_ANIMATION_FRAMES} кадрами; увеличьте шаг")
    if mode == "series" and len(leads) > MAP_MAX_PNG_SERIES_FRAMES:
        raise GfsProfileError(f"Серия ограничена {MAP_MAX_PNG_SERIES_FRAMES} PNG; увеличьте шаг")
    return leads


def auto_map_step(lead_from: int, lead_to: int, requested: int = 3, mode: str = "gif") -> int:
    if normalize_map_mode(mode) != "gif":
        return max(1, int(requested))
    for step in (max(1, int(requested)), 3, 6, 12, 24):
        try:
            if len(map_leads(lead_from, lead_to, step, mode)) <= MAP_MAX_ANIMATION_FRAMES:
                return step
        except GfsProfileError:
            continue
    raise GfsProfileError("Не удалось подобрать шаг анимации")


def normalize_map_params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_MAP_PARAMS)
    if value:
        for key in result:
            if key in value:
                result[key] = value[key]
    result["from"] = int(result["from"])
    result["to"] = int(result["to"])
    result["step"] = int(result["step"])
    result["mode"] = normalize_map_mode(str(result["mode"]))
    result["radius"] = float(result["radius"])
    result["basemap"] = normalize_basemap(str(result["basemap"]))
    if not 1 <= float(result["radius"]) <= 100:
        raise GfsProfileError("radius должен быть 1..100 км")
    if result["mode"] == "single":
        result["to"] = result["from"]
    elif result["mode"] == "gif":
        result["step"] = auto_map_step(result["from"], result["to"], result["step"], "gif")
    map_leads(result["from"], result["to"], result["step"], result["mode"])
    return result


def _strip(text: str, match: re.Match[str] | None) -> str:
    if match is None:
        return text
    return (text[: match.start()] + text[match.end() :]).strip()


def parse_map_input(raw_text: str) -> ParsedMapInput:
    text = str(raw_text or "").strip()
    params = dict(DEFAULT_MAP_PARAMS)
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(run_match.group("date"), run_match.group("cycle"))
        text = _strip(text, run_match)

    explicit_range = False
    for regex, key in ((FROM_RE, "from"), (TO_RE, "to"), (STEP_RE, "step"), (RADIUS_RE, "radius")):
        match = regex.search(text)
        if match:
            params[key] = int(match.group("value"))
            explicit_range = explicit_range or key in {"from", "to", "step"}
            text = _strip(text, match)

    mode_match = MODE_RE.search(text)
    if mode_match:
        params["mode"] = normalize_map_mode(mode_match.group("value"))
        text = _strip(text, mode_match)
    elif explicit_range:
        params["mode"] = "series"

    base_match = BASEMAP_RE.search(text)
    if base_match:
        params["basemap"] = normalize_basemap(base_match.group("value"))
        text = _strip(text, base_match)

    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead = int(lead_match.group("lead"))
        params.update({"from": lead, "to": lead, "mode": "single"})
        text = _strip(text, lead_match)

    params = normalize_map_params(params)
    if not text:
        raise ValueError("Не указана точка. Пример: /map Москва или /map Москва +24")
    return ParsedMapInput(
        text,
        run,
        int(params["from"]),
        int(params["to"]),
        int(params["step"]),
        str(params["mode"]),
        float(params["radius"]),
        str(params["basemap"]),
    )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _progress_adapter(callback: Callable[[ProgressEvent], None] | None):
    if callback is None:
        return None
    def emit(event: dict[str, Any]) -> None:
        callback(ProgressEvent(
            stage=str(event.get("stage", "progress")),
            message=str(event.get("message", "")),
            current=_safe_int(event.get("index") or event.get("downloaded")),
            total=_safe_int(event.get("total")),
            data=dict(event),
        ))
    return emit


def _mode_title(mode: str) -> str:
    return {"gif": "Анимация", "series": "Серия PNG", "single": "Одна карта"}[normalize_map_mode(mode)]


def map_repeat_command(point: Any, run: GfsRun, params: dict[str, Any]) -> str:
    p = normalize_map_params(params)
    if p["mode"] == "single":
        time_part = f"+{p['from']}"
    else:
        time_part = f"from={p['from']} to={p['to']} step={p['step']} mode={p['mode']}"
    return (
        f"/map {float(point.lat):.4f} {float(point.lon):.4f} run={run.date}/{run.cycle} "
        f"{time_part} radius={int(p['radius'])} basemap={p['basemap']}"
    )


def format_map_summary(first: dict[str, Any], point: Any, params: dict[str, Any], leads: list[int]) -> str:
    run = first["run"]
    mode = normalize_map_mode(params["mode"])
    valid_first = run.run_datetime_utc + __import__("datetime").timedelta(hours=int(leads[0]))
    valid_last = run.run_datetime_utc + __import__("datetime").timedelta(hours=int(leads[-1]))
    missing = sorted(str(value) for value in (first.get("missing") or set()))
    missing_line = f"\n⚠️ Нет полей GFS: {', '.join(missing)}" if missing else ""
    period = f"+{leads[0]} ч" if len(leads) == 1 else f"+{leads[0]}…+{leads[-1]} ч · шаг {params['step']} ч"
    return (
        f"🗺 GFS 0.25 · {_mode_title(mode)}\n"
        f"Run {run.date} {run.cycle}Z · {period} · UTC\n"
        f"valid {valid_first:%d.%m %H:%M} — {valid_last:%d.%m %H:%M UTC}\n"
        f"📍 {getattr(point, 'label', 'точка')} · {float(point.lat):.4f}, {float(point.lon):.4f}\n"
        f"Область: радиус {int(params['radius'])} км · сетка GFS 0.25° · подложка {params['basemap']}\n"
        f"Слои: осадки, облачность, гроза, ветер 500 гПа, явления, видимость"
        f"{missing_line}\n"
        "GFS • модельная карта, не радар и не наблюдение"
    )


def build_map_product_result(
    point: Any,
    lead_from: int = 0,
    lead_to: int = 48,
    step: int = 3,
    mode: str = "gif",
    radius_km: float = MAP_RADIUS_KM,
    basemap: str = "places",
    run: GfsRun | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    run_selector: Callable[[int], GfsRun] | None = None,
) -> CommonProductResult:
    params = normalize_map_params({
        "from": lead_from, "to": lead_to, "step": step, "mode": mode,
        "radius": radius_km, "basemap": basemap,
    })
    leads = map_leads(params["from"], params["to"], params["step"], params["mode"])
    if progress_callback:
        progress_callback(ProgressEvent("check", "Проверяю опубликованный цикл GFS"))
    selected_run = run or (run_selector or latest_available_run_for_lead)(max(leads))
    if progress_callback:
        progress_callback(ProgressEvent("run", f"GFS {selected_run.date} {selected_run.cycle}Z", data={"run_date": selected_run.date, "run_cycle": selected_run.cycle}))

    callback = _progress_adapter(progress_callback)
    paths: list[Path] = []
    first: dict[str, Any] | None = None
    try:
        if params["mode"] == "gif":
            frames = build_composite_map_frames(selected_run, leads, point, radius_km=params["radius"], basemap=params["basemap"], progress_callback=callback)
            first = frames[0]
            if progress_callback:
                progress_callback(ProgressEvent("plot_start", "Собираю MP4-анимацию"))
            try:
                path = Path(write_composite_map_mp4(frames, progress_callback=callback))
                mime = "video/mp4"
            except GfsProfileError as exc:
                if "ffmpeg" not in str(exc).lower():
                    raise
                path = Path(write_composite_map_gif(frames, progress_callback=callback))
                mime = "image/gif"
            paths = [path]
            attachments = [ProductAttachment("animation", path, path.name, f"MAP · GFS {selected_run.date} {selected_run.cycle}Z · +{leads[0]}…+{leads[-1]} ч", mime)]
        elif params["mode"] == "series":
            frames = build_composite_map_frames(selected_run, leads, point, radius_km=params["radius"], basemap=params["basemap"], progress_callback=callback)
            first = frames[0]
            attachments = []
            for index, frame in enumerate(frames, start=1):
                if progress_callback:
                    progress_callback(ProgressEvent("plot_start", f"PNG {index}/{len(frames)}", current=index, total=len(frames), data={"lead_hour": frame["lead_hour"]}))
                path = Path(write_composite_map_png(frame, progress_callback=callback))
                paths.append(path)
                attachments.append(ProductAttachment("image", path, path.name, f"MAP +{int(frame['lead_hour'])} ч · GFS {selected_run.date} {selected_run.cycle}Z", "image/png"))
        else:
            first = build_composite_map(selected_run, leads[0], point, radius_km=params["radius"], basemap=params["basemap"], progress_callback=callback)
            path = Path(write_composite_map_png(first, progress_callback=callback))
            paths = [path]
            attachments = [ProductAttachment("image", path, path.name, f"MAP +{leads[0]} ч · GFS {selected_run.date} {selected_run.cycle}Z", "image/png")]

        metadata = {
            "model": "GFS 0.25",
            "data_kind": "model",
            "source": "NOMADS GRIB Filter",
            "product": "map",
            "run_date": selected_run.date,
            "run_cycle": selected_run.cycle,
            "lead_from": leads[0], "lead_to": leads[-1], "step": params["step"],
            "mode": params["mode"], "radius": params["radius"], "basemap": params["basemap"],
            "requested_lat": float(point.lat), "requested_lon": float(point.lon),
            "frame_count": len(leads),
            "missing_fields": sorted(str(value) for value in (first.get("missing") or set())),
        }
        return CommonProductResult(
            product="map",
            summary=format_map_summary(first, point, params, leads),
            attachments=attachments,
            metadata=metadata,
            repeat_command=map_repeat_command(point, selected_run, params),
        )
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
