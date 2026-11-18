"""One regional icing calculation and export service for every messenger."""
from __future__ import annotations

import csv
import os
import tempfile
import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from gfs_core import CACHE_DIR, CACHE_TTL_SECONDS
from icing_config import (ICING_PRODUCTS, METHOD_VERSION, IcingRequest,
                          icing_repeat_command, validate_icing_request)
from icing_io import check_cancel, load_icing_input
from icing_meteorology import AGREEMENT_LABELS, PRODUCT_UNITS, compute_icing_fields, select_icing_field
from icing_render import render_icing_png

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent


def _emit(callback, stage, message, **data):
    if callback:
        callback(ProgressEvent(stage, message, data=data))


def _coverage(fields: dict, request: IcingRequest) -> tuple[np.ndarray, np.ndarray]:
    if request.product.startswith("godske_"):
        return fields["godske20_has_coverage"], fields["godske20_interior_missing"]
    if request.product == "agreement":
        covered = fields["agreement_has_coverage"]
        return covered, ~covered
    if request.product == "slw_level":
        k = list(fields["pressure_hpa"]).index(request.level_hpa)
        valid = fields["water_valid_above_ground"][k]
        return valid, np.zeros_like(valid, dtype=bool)
    if request.product in ("slw_max", "slw_height"):
        valid = fields["water_valid_above_ground"].any(axis=0)
        return valid, fields["SLWP_interior_missing"]
    return fields["SLWP_has_coverage"], fields["SLWP_interior_missing"]


def write_icing_csv(path: Path, fields: dict, lat: np.ndarray, lon: np.ndarray,
                    request: IcingRequest, *, cancel_event=None) -> None:
    """One product per CSV, with explicit units, time, domain and censoring flags."""
    values = select_icing_field(fields, request.product, request.level_hpa)
    known, missing = _coverage(fields, request)
    valid_time = request.run.run_datetime_utc + timedelta(hours=request.lead)
    run_text = request.run.run_datetime_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_text = valid_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    units = PRODUCT_UNITS[request.product]
    bbox_text = ",".join(f"{v:g}" for v in request.bbox)
    godske = request.product.startswith("godske_")
    def number(value):
        return format(float(value), ".9g") if np.isfinite(value) else ""
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("latitude_deg", "longitude_deg", "value", "unit", "product",
                         "run_utc", "lead_h", "valid_utc", "model", "method_version",
                         "level_hpa", "pressure_range_hpa", "bbox_w_e_s_n", "contour_step_gpm",
                         "data_coverage", "interior_missing", "column_lower_open", "column_upper_open",
                         "first_base_open", "first_top_open", "godske_layer_count", "category_label"))
        for index, (iy, ix) in enumerate(np.ndindex(values.shape)):
            if index % 1024 == 0:
                check_cancel(cancel_event)
            writer.writerow((number(lat[iy, ix]), number(lon[iy, ix]), number(values[iy, ix]),
                             units, request.product, run_text, request.lead, valid_text, "GFS 0.25",
                             METHOD_VERSION, request.level_hpa if request.product == "slw_level" else "",
                             "1000..300", bbox_text, request.contour_step if godske else "",
                             int(known[iy, ix]), int(missing[iy, ix]),
                             int(fields["godske20_lower_open"][iy, ix]) if godske else "",
                             int(fields["godske20_upper_open"][iy, ix]) if godske else "",
                             int(fields["godske20_base_first_open"][iy, ix]) if godske else "",
                             int(fields["godske20_top_first_open"][iy, ix]) if godske else "",
                             number(fields["godske20_n_layers"][iy, ix]) if godske else "",
                             AGREEMENT_LABELS[int(values[iy, ix])] if request.product == "agreement" and np.isfinite(values[iy, ix]) else ""))
    check_cancel(cancel_event)


def _output_path(directory: Path, request: IcingRequest, suffix: str) -> Path:
    prefix = f"icing_{request.product}_{request.run.date}_{request.run.cycle}_f{request.lead:03d}_"
    fd, name = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)
    os.close(fd)
    return Path(name)


def _clean_outputs(directory: Path) -> None:
    cutoff = time.time() - max(3600, CACHE_TTL_SECONDS)
    for suffix in ("png", "csv"):
        for path in directory.glob(f"icing_*.{suffix}"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                pass


def build_icing_product_result(
    request: IcingRequest, *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    cancel_event: threading.Event | None = None,
    input_loader: Callable | None = None,
    calculator: Callable | None = None,
    renderer: Callable | None = None,
) -> CommonProductResult:
    """Blocking worker: immutable raw cache, unique delivery files, no platform I/O."""
    request = validate_icing_request(request)
    check_cancel(cancel_event)
    _emit(progress_callback, "check", f"1/5 Проверяю f{request.lead:03d}.idx и поля GFS")
    def progress(payload):
        data = {key: value for key, value in payload.items() if key not in ("stage", "message")}
        _emit(progress_callback, payload.get("stage", "download"), payload.get("message", "Загружаю GFS"), **data)
    inputs = (input_loader or load_icing_input)(request, progress_callback=progress, cancel_event=cancel_event)
    request = replace(request, run=inputs.run)
    check_cancel(cancel_event)
    _emit(progress_callback, "calculate", "3/5 Выделяю слои и рассчитываю водность",
          run_date=inputs.run.date, run_cycle=inputs.run.cycle)
    fields = (calculator or compute_icing_fields)(**inputs.fields)
    check_cancel(cancel_event)
    directory = CACHE_DIR / "icing"
    directory.mkdir(parents=True, exist_ok=True)
    _clean_outputs(directory)
    paths: list[Path] = []
    try:
        png = _output_path(directory, request, ".png")
        paths.append(png)
        _emit(progress_callback, "render", "4/5 Строю карту")
        (renderer or render_icing_png)(fields, inputs.lat, inputs.lon, request.product,
            run=inputs.run, lead=request.lead, bbox=request.bbox, level_hpa=request.level_hpa,
            contour_step=request.contour_step, output_path=png)
        check_cancel(cancel_event)
        csv_path = _output_path(directory, request, ".csv")
        paths.append(csv_path)
        _emit(progress_callback, "export", "5/5 Формирую PNG и CSV")
        write_icing_csv(csv_path, fields, inputs.lat, inputs.lon, request, cancel_event=cancel_event)
        valid_time = inputs.run.run_datetime_utc + timedelta(hours=request.lead)
        values = select_icing_field(fields, request.product, request.level_hpa)
        known, missing = _coverage(fields, request)
        title = ICING_PRODUCTS[request.product]
        time_line = (f"Цикл {inputs.run.run_datetime_utc:%d.%m.%Y %H} UTC · +{request.lead} ч\n"
                     f"Действителен {valid_time:%d.%m.%Y %H:%M} UTC")
        west, east, south, north = request.bbox
        details = (f"{request.level_hpa} гПа" if request.product == "slw_level" else "1000–300 гПа, доступная часть профиля")
        summary = (f"{title}\n{time_line}\n"
                   f"Район: {west:g}…{east:g}° долготы; {south:g}…{north:g}° с.ш.\n"
                   f"{details} · {PRODUCT_UNITS[request.product]}\n"
                   "GFS data · 0.25°")
        if not np.isfinite(values).any():
            summary += "\nНет определённых значений: проверьте покрытие и незамкнутые границы."
        metadata = {
            "product": "icing", "icing_product": request.product, "model": "GFS 0.25",
            "source": "NOAA/NCEP NOMADS GRIB Filter", "data_kind": "model",
            "run_date": inputs.run.date, "run_cycle": inputs.run.cycle, "lead": request.lead,
            "valid_time_utc": valid_time.isoformat(), "bbox": list(request.bbox),
            "level_hpa": request.level_hpa, "contour_step": request.contour_step,
            "method_version": METHOD_VERSION, "units": PRODUCT_UNITS[request.product],
            "pressure_levels_hpa": [int(x) for x in fields["pressure_hpa"]],
            "grid_shape": list(inputs.lat.shape), "covered_nodes": int(np.count_nonzero(known)),
            "nodes_with_internal_gaps": int(np.count_nonzero(missing)),
            "finite_value_nodes": int(np.isfinite(values).sum()),
            "no_surface_extrapolation": True, "meteorological_skill_validated": False,
            "source_files": [path.name for path in inputs.paths],
        }
        check_cancel(cancel_event)
        return CommonProductResult(
            product="icing", summary=summary,
            attachments=[ProductAttachment("image", png, png.name, title, "image/png"),
                         ProductAttachment("file", csv_path, csv_path.name, "Численные значения и метаданные", "text/csv")],
            metadata=metadata, repeat_command=icing_repeat_command(request),
        )
    except BaseException:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
