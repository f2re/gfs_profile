from __future__ import annotations

"""Messenger-neutral GFS windgram service.

The service owns public request parsing, actual GFS run selection and conversion
of the existing windgram product into a transport-neutral CommonProductResult.
Telegram, MAX and VK render the same result and must not duplicate calculations.
"""

import re
from pathlib import Path
from typing import Any, Callable, NamedTuple

from gfs_core import GfsProfileError, GfsRun, latest_available_run_for_lead
from windgram_product import build_windgram_data, normalize_windgram_param, windgram_leads
from windgram_plot import write_windgram_png

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
FROM_RE = re.compile(r"\bfrom=(?P<value>\d{1,3})\b", re.IGNORECASE)
TO_RE = re.compile(r"\bto=(?P<value>\d{1,3})\b", re.IGNORECASE)
STEP_RE = re.compile(r"\bstep=(?P<value>\d{1,2})\b", re.IGNORECASE)
TOP_RE = re.compile(r"\btop=(?P<value>\d{3,4})\b", re.IGNORECASE)
PARAM_RE = re.compile(
    r"\b(?:param|field|параметр)=(?P<value>wind|ветер|v|speed|temp|t|temperature|температура|rh|humidity|влажность)\b",
    re.IGNORECASE,
)

PARAM_NAMES = {"wind": "ветер", "temp": "температура", "rh": "влажность"}
PARAM_CAPTIONS = {
    "wind": "цвет/число = скорость ветра, стрелка = направление",
    "temp": "цвет/число = температура, стрелка = направление ветра",
    "rh": "цвет/число = влажность, стрелка = направление ветра",
}


class ParsedWindgramInput(NamedTuple):
    location_query: str
    run: GfsRun | None
    lead_from: int
    lead_to: int
    step: int
    top_hpa: int
    param: str


def _pop_int(pattern: re.Pattern[str], text: str, default: int) -> tuple[int, str]:
    match = pattern.search(text)
    if not match:
        return default, text
    value = int(match.group("value"))
    return value, (text[: match.start()] + text[match.end() :]).strip()


def _pop_param(text: str) -> tuple[str, str]:
    match = PARAM_RE.search(text)
    if not match:
        return "wind", text
    value = normalize_windgram_param(match.group("value"))
    return value, (text[: match.start()] + text[match.end() :]).strip()


def parse_windgram_input(raw_text: str) -> ParsedWindgramInput:
    text = str(raw_text or "").strip()
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    param, text = _pop_param(text)
    lead_from, text = _pop_int(FROM_RE, text, 0)
    lead_to, text = _pop_int(TO_RE, text, 120)
    step, text = _pop_int(STEP_RE, text, 6)
    top_hpa, text = _pop_int(TOP_RE, text, 500)

    if lead_to > 384:
        raise GfsProfileError("to для windgram не может быть больше +384 ч")
    if top_hpa < 500:
        raise GfsProfileError("top ниже 500 гПа пока не поддерживается")
    windgram_leads(lead_from=lead_from, lead_to=lead_to, step=step)
    if not text:
        raise ValueError("Не указана точка. Пример: /windgram Москва to=120 param=temp")
    return ParsedWindgramInput(text, run, lead_from, lead_to, step, top_hpa, param)


def windgram_repeat_command(
    point: Any,
    *,
    run: GfsRun,
    lead_from: int,
    lead_to: int,
    step: int,
    top_hpa: int,
    param: str,
) -> str:
    return (
        f"/windgram {float(point.lat):.4f} {float(point.lon):.4f} "
        f"run={run.date}/{run.cycle} from={int(lead_from)} to={int(lead_to)} "
        f"step={int(step)} top={int(top_hpa)} param={normalize_windgram_param(param)}"
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


def format_windgram_summary(data: Any, point: Any) -> str:
    first_lead = int(data.leads[0])
    last_lead = int(data.leads[-1])
    step = int(data.leads[1] - data.leads[0]) if len(data.leads) > 1 else 0
    valid_times = [cell.valid_time_utc for cell in data.cells if getattr(cell, "valid_time_utc", None) is not None]
    valid_line = ""
    if valid_times:
        valid_line = f"valid {min(valid_times):%d.%m %H:%M} — {max(valid_times):%d.%m %H:%M UTC}\n"
    speeds = [float(cell.wind_speed_ms) for cell in data.cells if getattr(cell, "wind_speed_ms", None) is not None]
    max_wind_line = f"Макс. ветер: {max(speeds):.1f} м/с\n" if speeds else ""
    top = min(int(value) for value in data.levels_hpa)
    return (
        f"🟦 GFS 0.25 · срок × уровень · {PARAM_NAMES.get(data.param, data.param)}\n"
        f"Run {data.run.date} {data.run.cycle}Z · UTC\n"
        f"+{first_lead}…+{last_lead} ч · шаг {step} ч\n"
        f"{valid_line}"
        f"📍 {getattr(point, 'label', 'точка')} · {float(data.requested_lat):.4f}, {float(data.requested_lon):.4f}\n"
        f"GFS grid: {float(data.grid_lat):.3f}, {float(data.grid_lon):.3f}\n"
        f"Уровни: 1000…{top} гПа · {len(data.levels_hpa)}\n"
        f"{max_wind_line}"
        f"{PARAM_CAPTIONS.get(data.param, PARAM_CAPTIONS['wind'])}\n"
        "GFS grid • модель, не наблюдение и не радиозонд"
    )


def build_windgram_product_result(
    point: Any,
    lead_from: int = 0,
    lead_to: int = 120,
    step: int = 6,
    top_hpa: int = 500,
    param: str = "wind",
    run: GfsRun | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    run_selector: Callable[[int], GfsRun] | None = None,
) -> CommonProductResult:
    selected_param = normalize_windgram_param(param)
    leads = windgram_leads(lead_from=int(lead_from), lead_to=int(lead_to), step=int(step))
    if int(top_hpa) < 500:
        raise GfsProfileError("top ниже 500 гПа пока не поддерживается")

    if progress_callback:
        progress_callback(ProgressEvent(stage="check", message="Проверяю опубликованный цикл GFS"))
    selector = run_selector or latest_available_run_for_lead
    selected_run = run or selector(max(leads))
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
        data = build_windgram_data(
            selected_run,
            float(point.lat),
            float(point.lon),
            lead_from=int(lead_from),
            lead_to=int(lead_to),
            step=int(step),
            top_hpa=int(top_hpa),
            param=selected_param,
            progress_callback=_progress_adapter(progress_callback),
        )
        if progress_callback:
            progress_callback(ProgressEvent(stage="plot_start", message="Строю PNG"))
        png_path = Path(write_windgram_png(data, param=selected_param))
        if progress_callback:
            progress_callback(ProgressEvent(stage="plot_done", message="PNG готов", data={"file": str(png_path)}))

        speeds = [float(cell.wind_speed_ms) for cell in data.cells if cell.wind_speed_ms is not None]
        metadata = {
            "model": "GFS 0.25",
            "data_kind": "model",
            "source": "NOMADS GRIB Filter",
            "product": "windgram",
            "run_date": selected_run.date,
            "run_cycle": selected_run.cycle,
            "lead_from": int(data.leads[0]),
            "lead_to": int(data.leads[-1]),
            "step": int(step),
            "top_hpa": min(int(value) for value in data.levels_hpa),
            "param": selected_param,
            "requested_lat": float(data.requested_lat),
            "requested_lon": float(data.requested_lon),
            "grid_lat": float(data.grid_lat),
            "grid_lon": float(data.grid_lon),
            "levels": len(data.levels_hpa),
            "max_wind_ms": max(speeds) if speeds else None,
        }
        return CommonProductResult(
            product="windgram",
            summary=format_windgram_summary(data, point),
            attachments=[
                ProductAttachment(
                    kind="image",
                    path=png_path,
                    filename=png_path.name,
                    caption=(
                        f"PNG · WINDGRAM · {PARAM_NAMES.get(selected_param, selected_param)} · "
                        f"GFS {selected_run.date} {selected_run.cycle}Z · "
                        f"+{data.leads[0]}…+{data.leads[-1]} ч · UTC"
                    ),
                    mime_type="image/png",
                )
            ],
            metadata=metadata,
            repeat_command=windgram_repeat_command(
                point,
                run=selected_run,
                lead_from=int(lead_from),
                lead_to=int(lead_to),
                step=int(step),
                top_hpa=int(top_hpa),
                param=selected_param,
            ),
        )
    except Exception:
        if png_path is not None:
            png_path.unlink(missing_ok=True)
        raise
