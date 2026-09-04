from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent

RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
LEAD_RE = re.compile(
    r"(?:^|\s)(?:lead=|\+|f)?(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?\s*$",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ParsedProfileInput:
    location_query: str
    lead_hour: int
    run: Any | None
    lead_from_user: bool


def parse_profile_input(raw_text: str, default_lead: int = 24) -> ParsedProfileInput:
    from gfs_core import GfsRun, validate_lead

    text = str(raw_text or "").strip()
    run = None
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
        raise ValueError("Не указана точка. Пример: Москва +24")
    return ParsedProfileInput(text, lead_hour, run, lead_from_user)


def plain_profile_summary(html_summary: str) -> str:
    text = html_summary.replace("</pre>", "").replace("<pre>", "")
    text = TAG_RE.sub("", text)
    return html_lib.unescape(text)


def profile_repeat_command(point: Any, lead_hour: int, run: Any) -> str:
    return (
        f"/profile {float(point.lat):.4f} {float(point.lon):.4f} "
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


def build_profile_product(
    point: Any,
    lead_hour: int,
    run: Any | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> CommonProductResult:
    """Build one GFS profile and return a platform-neutral result.

    The function is blocking. Messenger routers execute it in a worker thread.
    """

    from formatters import format_profile_summary, write_profile_csv
    from gfs_core import build_profile, latest_available_run_for_lead, validate_lead
    from profile_plot import write_profile_png

    lead_hour = validate_lead(int(lead_hour))
    selected_run = run or latest_available_run_for_lead(lead_hour)
    result = build_profile(
        selected_run,
        lead_hour,
        float(point.lat),
        float(point.lon),
        _progress_adapter(progress_callback),
    )

    png_path: Path | None = None
    csv_path: Path | None = None
    try:
        if progress_callback:
            progress_callback(ProgressEvent(stage="plot_start", message="Строю PNG"))
        png_path = Path(write_profile_png(result))
        csv_path = Path(write_profile_csv(result))
        if progress_callback:
            progress_callback(ProgressEvent(stage="plot_done", message="PNG и CSV готовы"))

        html_summary = format_profile_summary(result)
        metadata = {
            "model": "GFS 0.25",
            "data_kind": "model",
            "run_date": selected_run.date,
            "run_cycle": selected_run.cycle,
            "lead": lead_hour,
            "valid_utc": result.valid_time_utc.isoformat(),
            "requested_lat": result.requested_lat,
            "requested_lon": result.requested_lon,
            "grid_lat": result.grid_lat,
            "grid_lon": result.grid_lon,
            "source": "NOMADS GRIB Filter",
            "rows": int(len(result.dataframe)),
            "summary_html": html_summary,
        }
        return CommonProductResult(
            product="profile",
            summary=plain_profile_summary(html_summary),
            attachments=[
                ProductAttachment(
                    kind="image",
                    path=png_path,
                    filename=png_path.name,
                    caption=(
                        f"PNG · PROFILE · GFS {selected_run.date} {selected_run.cycle}Z · "
                        f"+{lead_hour} ч · UTC"
                    ),
                    mime_type="image/png",
                ),
                ProductAttachment(
                    kind="file",
                    path=csv_path,
                    filename=csv_path.name,
                    caption=(
                        f"CSV · PROFILE · GFS {selected_run.date} {selected_run.cycle}Z · "
                        f"+{lead_hour} ч"
                    ),
                    mime_type="text/csv",
                ),
            ],
            metadata=metadata,
            repeat_command=profile_repeat_command(point, lead_hour, selected_run),
        )
    except Exception:
        if png_path:
            png_path.unlink(missing_ok=True)
        if csv_path:
            csv_path.unlink(missing_ok=True)
        raise


def cleanup_product_result(result: CommonProductResult) -> None:
    for attachment in result.attachments:
        try:
            attachment.path.unlink(missing_ok=True)
        except OSError:
            pass
