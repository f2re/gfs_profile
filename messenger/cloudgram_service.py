from __future__ import annotations

"""Messenger-neutral GFS cloudgram service.

The service owns request parsing, mode/range validation, published-run selection
for the maximum required lead and conversion of the existing cloudgram product
into a transport-neutral ``CommonProductResult``.
"""

import re
from pathlib import Path
from typing import Any, Callable, NamedTuple

from cloudgram_product import (
    CLOUDGRAM_DEFAULT_STEP,
    CLOUDGRAM_DEFAULT_TO,
    CloudgramData,
    build_cloudgram_data,
    cloudgram_leads,
)
from cloudgram_render import write_cloudgram_png
from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,2})\b", re.IGNORECASE)
MODE_RE = re.compile(r"\bmode=(?P<value>[\wа-яё-]+)\b", re.IGNORECASE)

MODE_TITLES = {"pro": "Подробно", "simple": "Кратко"}
MODE_DESCRIPTIONS = {
    "simple": "облака по ярусам, осадки/явления, гроза, видимость, опасность",
    "pro": "облачность H/M/L, осадки, явления, видимость, ВНГО, грозовой риск, опасность",
}


class ParsedCloudgramInput(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    mode: str = "pro"


def normalize_cloudgram_mode(value: str | None) -> str:
    raw = (value or "pro").strip().lower().replace("ё", "е")
    if raw in {
        "simple",
        "simp",
        "easy",
        "lite",
        "user",
        "простои",
        "простой",
        "упрощенно",
        "упрощенныи",
        "упрощенный",
        "кратко",
    }:
        return "simple"
    if raw in {"pro", "prof", "professional", "meteo", "профи", "профессионально", "подробно"}:
        return "pro"
    raise GfsProfileError("mode должен быть pro или simple")


def _pop_int(pattern: re.Pattern[str], text: str, default: int) -> tuple[int, str]:
    match = pattern.search(text)
    if not match:
        return default, text
    value = int(match.group("value"))
    return value, (text[: match.start()] + text[match.end() :]).strip()


def _pop_mode(text: str) -> tuple[str, str]:
    match = MODE_RE.search(text)
    if not match:
        return "pro", text
    mode = normalize_cloudgram_mode(match.group("value"))
    return mode, (text[: match.start()] + text[match.end() :]).strip()


def parse_cloudgram_input(raw_text: str) -> ParsedCloudgramInput:
    text = str(raw_text or "").strip()
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    mode, text = _pop_mode(text)
    lead_from, text = _pop_int(FROM_RE, text, 0)
    lead_to, text = _pop_int(TO_RE, text, CLOUDGRAM_DEFAULT_TO)
    step, text = _pop_int(STEP_RE, text, CLOUDGRAM_DEFAULT_STEP)
    cloudgram_leads(lead_from, lead_to, step)
    if not text:
        raise ValueError("Не указана точка. Пример: /cloudgram Москва to=72 step=3 mode=simple")
    return ParsedCloudgramInput(text, run, lead_from, lead_to, step, mode)


def hazard_label(value: int) -> str:
    return {
        0: "0/4 — спокойно",
        1: "1/4 — слабые явления",
        2: "2/4 — ограничения",
        3: "3/4 — опасно",
        4: "4/4 — гроза / очень опасно",
    }.get(max(0, min(int(value), 4)), f"{value}/4")


def cloudgram_repeat_command(
    point: Any,
    *,
    run: GfsRun,
    lead_from: int,
    lead_to: int,
    step: int,
    mode: str,
) -> str:
    return (
        f"/cloudgram {float(point.lat):.4f} {float(point.lon):.4f} "
        f"run={run.date}/{run.cycle} from={int(lead_from)} to={int(lead_to)} "
        f"step={int(step)} mode={normalize_cloudgram_mode(mode)}"
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
        callback(
            ProgressEvent(
                stage=str(event.get("stage", "progress")),
                message=str(event.get("message", "")),
                current=_safe_int(event.get("index") or event.get("downloaded")),
                total=_safe_int(event.get("total")),
                data=dict(event),
            )
        )

    return emit


def _lead_step(data: CloudgramData) -> int:
    return data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0


def format_cloudgram_summary(data: CloudgramData, point: Any, mode: str) -> str:
    selected_mode = normalize_cloudgram_mode(mode)
    max_hazard = max((int(cell.hazard_score) for cell in data.cells), default=0)
    valid_times = [cell.valid_time_utc for cell in data.cells if getattr(cell, "valid_time_utc", None) is not None]
    valid_line = ""
    if valid_times:
        valid_line = f"valid {min(valid_times):%d.%m %H:%M} — {max(valid_times):%d.%m %H:%M UTC}\n"
    missing = f"\n⚠️ Нет полей GFS: {', '.join(data.missing_fields)}" if data.missing_fields else ""
    return (
        f"☁️ GFS 0.25 · облака и явления · {MODE_TITLES[selected_mode]}\n"
        f"Run {data.run.date} {data.run.cycle}Z · UTC\n"
        f"+{data.leads[0]}…+{data.leads[-1]} ч · шаг {_lead_step(data)} ч\n"
        f"{valid_line}"
        f"📍 {getattr(point, 'label', 'точка')} · {float(data.requested_lat):.4f}, {float(data.requested_lon):.4f}\n"
        f"GFS grid: {float(data.grid_lat):.3f}, {float(data.grid_lon):.3f}\n"
        f"⚠️ Макс. опасность: {hazard_label(max_hazard)}\n"
        f"{MODE_DESCRIPTIONS[selected_mode]}"
        f"{missing}\n"
        "Опасность/гроза — модельная диагностика, не наблюдение.\n"
        "GFS grid • модель, не радиозонд"
    )


def build_cloudgram_product_result(
    point: Any,
    lead_from: int = 0,
    lead_to: int = CLOUDGRAM_DEFAULT_TO,
    step: int = CLOUDGRAM_DEFAULT_STEP,
    mode: str = "pro",
    run: GfsRun | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    run_selector: Callable[[int], GfsRun] = latest_available_run_for_lead,
) -> CommonProductResult:
    selected_mode = normalize_cloudgram_mode(mode)
    leads = cloudgram_leads(int(lead_from), int(lead_to), int(step))
    if progress_callback:
        progress_callback(ProgressEvent(stage="check", message="Проверяю опубликованный цикл GFS"))
    selected_run = run or run_selector(max(leads))
    if progress_callback:
        progress_callback(
            ProgressEvent(
                stage="run",
                message=f"Выбран GFS {selected_run.date} {selected_run.cycle}Z",
                data={"run_date": selected_run.date, "run_cycle": selected_run.cycle},
            )
        )

    png_path: Path | None = None
    try:
        data = build_cloudgram_data(
            selected_run,
            float(point.lat),
            float(point.lon),
            lead_from=int(lead_from),
            lead_to=int(lead_to),
            step=int(step),
            progress_callback=_progress_adapter(progress_callback),
        )
        if progress_callback:
            progress_callback(ProgressEvent(stage="plot_start", message="Строю PNG"))
        png_path = Path(write_cloudgram_png(data, mode=selected_mode))
        if progress_callback:
            progress_callback(ProgressEvent(stage="plot_done", message="PNG готов", data={"file": str(png_path)}))

        max_hazard = max((int(cell.hazard_score) for cell in data.cells), default=0)
        metadata = {
            "model": "GFS 0.25",
            "data_kind": "model",
            "source": "NOMADS GRIB Filter",
            "product": "cloudgram",
            "run_date": selected_run.date,
            "run_cycle": selected_run.cycle,
            "lead_from": int(data.leads[0]),
            "lead_to": int(data.leads[-1]),
            "step": int(step),
            "mode": selected_mode,
            "requested_lat": float(data.requested_lat),
            "requested_lon": float(data.requested_lon),
            "grid_lat": float(data.grid_lat),
            "grid_lon": float(data.grid_lon),
            "max_hazard": max_hazard,
            "missing_fields": list(data.missing_fields),
        }
        return CommonProductResult(
            product="cloudgram",
            summary=format_cloudgram_summary(data, point, selected_mode),
            attachments=[
                ProductAttachment(
                    kind="image",
                    path=png_path,
                    filename=png_path.name,
                    caption=(
                        f"PNG · CLOUDGRAM · {MODE_TITLES[selected_mode]} · "
                        f"GFS {selected_run.date} {selected_run.cycle}Z · "
                        f"+{data.leads[0]}…+{data.leads[-1]} ч · UTC"
                    ),
                    mime_type="image/png",
                )
            ],
            metadata=metadata,
            repeat_command=cloudgram_repeat_command(
                point,
                run=selected_run,
                lead_from=int(lead_from),
                lead_to=int(lead_to),
                step=int(step),
                mode=selected_mode,
            ),
        )
    except Exception:
        if png_path is not None:
            png_path.unlink(missing_ok=True)
        raise
