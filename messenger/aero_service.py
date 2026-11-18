from __future__ import annotations

"""Messenger-neutral GFS aerological diagram service.

The service owns request parsing, actual GFS run selection and conversion of the
existing Skew-T product into a transport-neutral ``CommonProductResult``.
Telegram, MAX and VK must not reimplement these decisions.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gfs_core import GfsRun, latest_available_run_for_lead, validate_lead

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent

AERO_DIAGRAM_TYPE = "skewt"
AERO_TYPE_RE = re.compile(r"\btype=(?P<type>stuve|emagram|skewt)\b", re.IGNORECASE)
RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
LEAD_RE = re.compile(
    r"(?:^|\s)(?:lead=|\+|f)?(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedAeroInput:
    location_query: str
    lead_hour: int
    run: GfsRun | None
    lead_from_user: bool
    diagram_type: str = AERO_DIAGRAM_TYPE


def parse_aero_input(raw_text: str, default_lead: int = 24) -> ParsedAeroInput:
    """Parse the public /aero syntax.

    Historical ``type=stuve|emagram|skewt`` is accepted only for backwards
    compatibility and ignored. The public product is always Skew-T log-P.
    """

    text = str(raw_text or "").strip()
    type_match = AERO_TYPE_RE.search(text)
    if type_match:
        text = (text[: type_match.start()] + text[type_match.end() :]).strip()

    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    lead_hour = int(default_lead)
    lead_from_user = False
    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead_hour = int(lead_match.group("lead"))
        lead_from_user = True
        text = text[: lead_match.start()].strip()

    validate_lead(lead_hour)
    if not text:
        raise ValueError("Не указана точка. Пример: /aero Москва +24")
    return ParsedAeroInput(text, lead_hour, run, lead_from_user, AERO_DIAGRAM_TYPE)


def aero_repeat_command(point: Any, lead_hour: int, run: Any) -> str:
    return (
        f"/aero {float(point.lat):.4f} {float(point.lon):.4f} "
        f"run={run.date}/{run.cycle} +{int(lead_hour)}"
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


def format_aero_summary(result: Any, point: Any) -> str:
    requested_lat = float(getattr(result, "requested_lat", getattr(point, "lat", 0.0)))
    requested_lon = float(getattr(result, "requested_lon", getattr(point, "lon", 0.0)))
    return (
        "🧾 GFS 0.25 · аэрологическая диаграмма\n"
        f"Run {result.run.date} {result.run.cycle}Z · +{int(result.lead_hour)} ч · "
        f"valid {result.valid_time_utc:%d.%m %H:%M UTC}\n"
        f"📍 {getattr(point, 'label', 'точка')} · {requested_lat:.4f}, {requested_lon:.4f}\n"
        f"GFS grid: {float(result.grid_lat):.3f}, {float(result.grid_lon):.3f}\n"
        "Skew-T log-P · годограф · Zg MSL\n"
        "icing/CAT — модельные прокси · модель, не радиозонд"
    )


def build_aero_product_result(
    point: Any,
    lead_hour: int,
    run: GfsRun | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> CommonProductResult:
    """Build one aerological diagram and return a platform-neutral result.

    The function is blocking. Messenger/Telegram adapters execute it in a worker
    thread and provide a platform-specific status renderer around ProgressEvent.
    """

    from aero_product import build_aero_product

    lead_hour = validate_lead(int(lead_hour))
    if progress_callback:
        progress_callback(ProgressEvent(stage="check", message="Проверяю опубликованный цикл GFS"))
    selected_run = run or latest_available_run_for_lead(lead_hour)
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
        profile, raw_png = build_aero_product(
            selected_run,
            lead_hour,
            float(point.lat),
            float(point.lon),
            AERO_DIAGRAM_TYPE,
            progress_callback=_progress_adapter(progress_callback),
        )
        png_path = Path(raw_png)
        summary = format_aero_summary(profile, point)
        metadata = {
            "model": "GFS 0.25",
            "data_kind": "model",
            "source": "NOMADS GRIB Filter",
            "product": "aero",
            "diagram_type": AERO_DIAGRAM_TYPE,
            "run_date": selected_run.date,
            "run_cycle": selected_run.cycle,
            "lead": lead_hour,
            "valid_utc": profile.valid_time_utc.isoformat(),
            "requested_lat": float(getattr(profile, "requested_lat", point.lat)),
            "requested_lon": float(getattr(profile, "requested_lon", point.lon)),
            "grid_lat": float(profile.grid_lat),
            "grid_lon": float(profile.grid_lon),
            "rows": int(len(profile.dataframe)),
            "icing_cat_are_model_proxies": True,
        }
        return CommonProductResult(
            product="aero",
            summary=summary,
            attachments=[
                ProductAttachment(
                    kind="image",
                    path=png_path,
                    filename=png_path.name,
                    caption=(
                        f"PNG · AERO · GFS {selected_run.date} {selected_run.cycle}Z · "
                        f"+{lead_hour} ч · Skew-T"
                    ),
                    mime_type="image/png",
                )
            ],
            metadata=metadata,
            repeat_command=aero_repeat_command(point, lead_hour, selected_run),
        )
    except Exception:
        if png_path is not None:
            png_path.unlink(missing_ok=True)
        raise
