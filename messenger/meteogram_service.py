from __future__ import annotations

"""Messenger-neutral meteogram fetch/render/report service."""

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, NamedTuple

import numpy as np

from meteogram_core import MeteogramError, fetch_meteogram, source_for_id
from meteogram_plot import write_meteogram_png
from meteogram_report import MeteogramReportError, write_meteogram_report
from meteogram_request import MeteogramRequest, parse_meteogram_request

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent

OUTPUT_FORMATS = ("png", "docx", "pdf")
OUTPUT_RE = re.compile(r"(?<!\S)(?:format|output|report|формат)\s*=\s*([^\s]+)", re.IGNORECASE)
DEFAULT_METEOGRAM_PARAMS = {"source": "gfs", "days": 5, "format": "png"}


class ParsedMeteogramInput(NamedTuple):
    location_query: str
    source_id: str
    days: int
    output_format: str


def normalize_output_format(value: Any) -> str:
    key = str(value or "png").strip().lower().lstrip(".")
    aliases = {"image": "png", "photo": "png", "картинка": "png", "word": "docx", "document": "docx", "документ": "docx", "portable": "pdf"}
    key = aliases.get(key, key)
    if key not in OUTPUT_FORMATS:
        raise MeteogramError("Формат результата: png, docx или pdf")
    return key


def normalize_meteogram_params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_METEOGRAM_PARAMS)
    if value:
        if "source" in value:
            result["source"] = str(value["source"])
        if "source_id" in value:
            result["source"] = str(value["source_id"])
        if "days" in value:
            result["days"] = int(value["days"])
        if "format" in value:
            result["format"] = normalize_output_format(value["format"])
        if "output_format" in value:
            result["format"] = normalize_output_format(value["output_format"])
    source = source_for_id(str(result["source"]))
    if int(result["days"]) < 1 or int(result["days"]) > source.horizon_days:
        raise MeteogramError(f"Для {source.label} доступно 1–{source.horizon_days} суток")
    result["source"] = source.source_id
    result["days"] = int(result["days"])
    result["format"] = normalize_output_format(result["format"])
    return result


def parse_meteogram_input(raw: str) -> ParsedMeteogramInput:
    text = str(raw or "").strip()
    matches = [normalize_output_format(item) for item in OUTPUT_RE.findall(text)]
    if len(set(matches)) > 1:
        raise MeteogramError("Указаны противоречивые форматы результата")
    output = matches[0] if matches else "png"
    cleaned = " ".join(OUTPUT_RE.sub(" ", text).split())
    request: MeteogramRequest = parse_meteogram_request(cleaned)
    params = normalize_meteogram_params({"source": request.source_id, "days": request.days, "format": output})
    return ParsedMeteogramInput(request.location_query, params["source"], params["days"], params["format"])


def meteogram_repeat_command(point: Any, params: dict[str, Any]) -> str:
    p = normalize_meteogram_params(params)
    return f"/meteogram {float(point.lat):.4f} {float(point.lon):.4f} source={p['source']} days={p['days']} format={p['format']}"


def _member_text(series: Any) -> tuple[str, str]:
    source = series.source
    if not source.ensemble:
        return "", ""
    observed = int(series.member_count or 0)
    expected = int(series.expected_member_count or observed)
    main = f"Ансамбль: {observed}/{expected} членов"
    values = series.values("ensemble_member_count")
    finite = values[np.isfinite(values)]
    minimum = int(np.nanmin(finite)) if finite.size else observed
    warning = f"На отдельных сроках доступно от {minimum}/{expected} членов" if expected and minimum < expected else ""
    return main, warning


def format_meteogram_summary(series: Any, output_format: str, fallback_reason: str | None = None) -> str:
    source = series.source
    member, member_warning = _member_text(series)
    grid = ""
    if series.grid_lat is not None and series.grid_lon is not None:
        grid = f"\n📐 Расчётная точка: {float(series.grid_lat):.4f}, {float(series.grid_lon):.4f}"
    warnings = [str(value) for value in (series.warnings or []) if str(value).strip()]
    if member_warning:
        warnings.append(member_warning)
    if fallback_reason:
        warnings.append("PDF создать не удалось; сформирован DOCX")
    warning_text = "" if not warnings else "\n⚠️ " + "; ".join(warnings[:4])
    member_text = f"\n{member}" if member else ""
    return (
        f"📊 {'Ансамблевая ' if source.ensemble else ''}метеограмма\n"
        f"📍 {series.point_label} · {float(series.requested_lat):.4f}, {float(series.requested_lon):.4f}{grid}\n"
        f"Модель: {source.model}\nПоставщик: {source.provider}{member_text}\n"
        f"Период: {series.times[0]:%d.%m %H:%M} — {series.times[-1]:%d.%m %H:%M} · {series.timezone}\n"
        f"Результат: {output_format.upper()}{warning_text}\n"
        "ℹ Модельный прогноз, не наблюдение. Исходный cycle не указывается, если поставщик его не передал."
    )


def build_meteogram_product_result(
    point: Any,
    source_id: str = "gfs",
    days: int = 5,
    output_format: str = "png",
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> CommonProductResult:
    params = normalize_meteogram_params({"source": source_id, "days": days, "format": output_format})
    source = source_for_id(params["source"])
    if progress_callback:
        progress_callback(ProgressEvent("fetch_start", f"Получаю {source.label}"))

    def fetch_progress(message: str) -> None:
        if progress_callback:
            progress_callback(ProgressEvent("fetch", str(message)))

    png_path: Path | None = None
    report_dir: Path | None = None
    report_result = None
    attachment_paths: list[Path] = []
    try:
        series = fetch_meteogram(
            source.source_id,
            str(point.label),
            float(point.lat),
            float(point.lon),
            int(params["days"]),
            fetch_progress,
        )
        if progress_callback:
            progress_callback(ProgressEvent("plot_start", "Строю PNG-метеограмму"))
        png_path = Path(write_meteogram_png(series))

        actual_format = params["format"]
        fallback_reason = None
        if params["format"] == "png":
            attachment_paths.append(png_path)
            attachments = [ProductAttachment(
                "image", png_path, png_path.name,
                f"PNG · METEOGRAM · {source.model} · {params['days']} сут · {point.label}",
                "image/png",
            )]
        else:
            if progress_callback:
                progress_callback(ProgressEvent("report_start", f"Формирую {params['format'].upper()}"))
            report_dir = Path(tempfile.mkdtemp(prefix="gfs_meteogram_report_"))
            report_result = write_meteogram_report(series, png_path, params["format"], output_dir=report_dir)
            actual_format = report_result.format
            fallback_reason = report_result.fallback_reason
            attachment_paths.append(Path(report_result.path))
            mime = "application/pdf" if actual_format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            attachments = [ProductAttachment(
                "file", Path(report_result.path), Path(report_result.path).name,
                f"{actual_format.upper()} · {'Ансамблевый' if source.ensemble else 'Модельный'} отчёт · {source.model} · {params['days']} сут · {point.label}",
                mime,
            )]

        metadata = {
            "model": source.model,
            "provider": source.provider,
            "source_id": source.source_id,
            "ensemble": bool(source.ensemble),
            "days": int(params["days"]),
            "output_format": actual_format,
            "requested_lat": float(point.lat),
            "requested_lon": float(point.lon),
            "grid_lat": series.grid_lat,
            "grid_lon": series.grid_lon,
            "timezone": series.timezone,
            "member_count": series.member_count,
            "expected_member_count": series.expected_member_count,
            "cycle": None,
            "data_kind": "model",
            "fallback_reason": fallback_reason,
        }
        result = CommonProductResult(
            product="meteogram",
            summary=format_meteogram_summary(series, actual_format, fallback_reason),
            attachments=attachments,
            metadata=metadata,
            repeat_command=meteogram_repeat_command(point, params),
        )

        # The report may own extra temporary files. Keep only attachment paths;
        # everything else can be removed after result creation.
        if report_result is not None:
            for path in report_result.cleanup_paths:
                path = Path(path)
                if path not in attachment_paths:
                    path.unlink(missing_ok=True)
        if png_path is not None and png_path not in attachment_paths:
            png_path.unlink(missing_ok=True)
        return result
    except Exception:
        for path in attachment_paths:
            path.unlink(missing_ok=True)
        if png_path:
            png_path.unlink(missing_ok=True)
        raise
    finally:
        # Do not remove report_dir while its selected attachment lives there.
        # cleanup_product_result removes the file; router then removes empty dir.
        if report_dir is not None and not attachment_paths:
            shutil.rmtree(report_dir, ignore_errors=True)
