from __future__ import annotations

"""Messenger-neutral WeatherNext 3 surface products.

BigQuery is used only for the surface/statistical products actually published
there. Upper-air WeatherNext 3 fields are intentionally not emulated: they live
in the GCS full-ensemble Zarr product and require a separate provider.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from .contracts import CommonProductResult, ProductAttachment, ProgressEvent
from .meteogram_service import build_meteogram_product_result
from weathernext3_map import KIND_TITLES, write_weathernext3_animation, write_weathernext3_map_png
from weathernext3_provider import MAP_KINDS, WeatherNext3Error, WeatherNext3Provider, provider_from_env

UPPER_KINDS = ("profile", "aero", "windgram")
WN3_KINDS = ("point", "meteogram", "ensemble", "cloudgram", "precip_compare", *UPPER_KINDS, *MAP_KINDS)
MAP_MODES = ("animation", "single", "series")
DEFAULT_WN3_PARAMS: dict[str, Any] = {
    "kind": "point",
    "hours": 24,
    "days": 5,
    "from": 1,
    "to": 48,
    "step": 3,
    "radius": 150,
    "mode": "animation",
    "basemap": "places",
    "stat": "mean",
    "member": "mean",
    "param": "wind",
    "top": 500,
    "format": "png",
}

_KIND_ALIASES = {
    "forecast": "point", "прогноз": "point", "точка": "point",
    "ens": "ensemble", "ансамбль": "ensemble", "разброс": "ensemble",
    "meteo": "meteogram", "meteogram": "meteogram", "метеограмма": "meteogram",
    "cloud": "clouds", "clouds": "clouds", "облака": "clouds", "облачность": "clouds",
    "layers": "cloud_layers", "cloud_layers": "cloud_layers", "слои": "cloud_layers",
    "precip": "precip_native", "rain": "precip_native", "precip_native": "precip_native", "осадки": "precip_native",
    "imerg": "precip_imerg", "precip_imerg": "precip_imerg",
    "experimental": "precip_experimental", "exp": "precip_experimental", "precip_experimental": "precip_experimental",
    "combo": "combo", "all": "combo", "совмещенная": "combo", "совмещённая": "combo",
}
_MODE_ALIASES = {
    "gif": "animation", "anim": "animation", "animation": "animation", "mp4": "animation", "анимация": "animation",
    "single": "single", "one": "single", "одна": "single",
    "series": "series", "png": "series", "серия": "series",
}
_PARAM_RE = re.compile(r"(?<!\S)(?P<key>[a-z_]+)=(?P<value>[^\s]+)", re.IGNORECASE)
_LEAD_RE = re.compile(r"(?:^|\s)\+(?P<value>\d{1,3})(?=\s|$)")


@dataclass(frozen=True, slots=True)
class ParsedWeatherNext3Input:
    location_query: str
    params: dict[str, Any]
    direct_run: bool


def normalize_wn3_kind(value: Any) -> str:
    key = str(value or "point").strip().lower().replace("-", "_")
    key = _KIND_ALIASES.get(key, key)
    if key not in WN3_KINDS:
        raise WeatherNext3Error("kind: point, meteogram, clouds, cloud_layers, precip_native, precip_imerg, precip_experimental или combo")
    return key


def normalize_wn3_mode(value: Any) -> str:
    key = str(value or "animation").strip().lower()
    key = _MODE_ALIASES.get(key, key)
    if key not in MAP_MODES:
        raise WeatherNext3Error("mode: animation, single или series")
    return key


def _map_leads(params: Mapping[str, Any]) -> list[int]:
    mode = normalize_wn3_mode(params.get("mode"))
    start = int(params.get("from", 1))
    end = int(params.get("to", 48))
    step = max(1, int(params.get("step", 3)))
    if mode == "single":
        end = start
    if start < 1 or end < start or end > 360:
        raise WeatherNext3Error("Сроки карты WeatherNext 3 должны быть 1..360 ч")
    leads = list(range(start, end + 1, step))
    if not leads or leads[-1] != end:
        leads.append(end)
    if mode == "animation" and len(leads) > 32:
        # Increase step deterministically; WN3 is hourly so no canonical 3h grid is required.
        step = max(step, math.ceil((end - start) / 31))
        leads = list(range(start, end + 1, step))
        if leads[-1] != end:
            leads.append(end)
    if mode == "animation" and len(leads) > 32:
        raise WeatherNext3Error("Анимация WeatherNext 3 ограничена 32 кадрами; увеличьте step")
    if mode == "series" and len(leads) > 24:
        raise WeatherNext3Error("Серия WeatherNext 3 ограничена 24 PNG; увеличьте step")
    return leads


def normalize_wn3_params(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_WN3_PARAMS)
    if value:
        for key in result:
            if key in value and value[key] is not None:
                result[key] = value[key]
    result["kind"] = normalize_wn3_kind(result["kind"])
    result["hours"] = int(result["hours"])
    result["days"] = int(result["days"])
    result["from"] = int(result["from"])
    result["to"] = int(result["to"])
    result["step"] = int(result["step"])
    result["radius"] = float(result["radius"])
    result["mode"] = normalize_wn3_mode(result["mode"])
    result["basemap"] = str(result.get("basemap", "places")).strip().lower() or "places"
    result['stat'] = str(result['stat']).lower()
    result['member'] = str(result['member']).lower()
    result['param'] = str(result['param']).lower()
    result['top'] = int(result['top'])
    result['format'] = str(result['format']).lower()
    if result['stat'] not in {'mean', 'p10', 'p25', 'p50', 'p75', 'p90'}:
        raise WeatherNext3Error('stat: mean, p10, p25, p50, p75, p90')
    if result['member'] != 'mean' and (not result['member'].isdigit() or not 0 <= int(result['member']) < 64):
        raise WeatherNext3Error('member: mean или 0..63')
    if result['param'] not in {'wind', 'temp', 'rh'}:
        raise WeatherNext3Error('param: wind, temp, rh')
    if result['top'] not in {1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50}:
        raise WeatherNext3Error('top: опубликованный изобарический уровень WN3')
    if result['format'] not in {'png', 'pdf', 'docx'}:
        raise WeatherNext3Error('format: png, pdf, docx (PDF/DOCX — только метеограмма)')
    if result['kind'] not in {'meteogram', 'ensemble'} and result['format'] != 'png':
        raise WeatherNext3Error('PDF/DOCX поддерживаются для kind=meteogram или kind=ensemble')
    if result['basemap'] not in {'basic', 'places', 'roads'}:
        raise WeatherNext3Error('basemap: basic, places, roads')
    if not 1 <= result['step'] <= 360 or not 1 <= result['from'] <= result['to'] <= 360:
        raise WeatherNext3Error('from/to/step: 1..360; from не больше to')
    if not 1 <= result["hours"] <= 360:
        raise WeatherNext3Error("hours должен быть 1..360")
    if not 1 <= result["days"] <= 15:
        raise WeatherNext3Error("days должен быть 1..15")
    if not 25 <= result["radius"] <= 500:
        raise WeatherNext3Error("radius должен быть 25..500 км")
    if result['kind'] not in MAP_KINDS and result['stat'] != 'mean':
        raise WeatherNext3Error('stat применяется только к картам; точка и метеограмма показывают весь набор статистик')
    if result['kind'] == 'temperature_spread' and result['stat'] != 'mean':
        raise WeatherNext3Error('Разброс T2 всегда равен p90−p10; отдельный stat не применяется')
    if result['kind'] not in UPPER_KINDS and result['member'] != 'mean':
        raise WeatherNext3Error('Отдельный member доступен только для верхней атмосферы GCS')
    if result["kind"] == "windgram":
        result["mode"] = "animation"
    if result["kind"] in (*MAP_KINDS, "windgram"):
        if result["mode"] == "single":
            result["to"] = result["from"]
        leads = _map_leads(result)
        if len(leads) > 1:
            result["step"] = leads[1] - leads[0]
    return result


def parse_weathernext3_input(raw: str) -> ParsedWeatherNext3Input:
    text = " ".join(str(raw or "").strip().split())
    params: dict[str, Any] = {}
    explicit = False
    for match in list(_PARAM_RE.finditer(text)):
        key = match.group("key").lower()
        value = match.group("value")
        if key in {"kind", "product", "layer"}:
            params["kind"] = value
        elif key in {"hours", "lead"}:
            params["hours"] = int(value)
        elif key in {"days", "day"}:
            params["days"] = int(value)
        elif key in {"from", "to", "step"}:
            params[key] = int(value)
        elif key in {"radius", "radius_km"}:
            params["radius"] = float(value)
        elif key in {"mode", "format", "stat", "member", "param"}:
            params[key] = value
        elif key == "top":
            params[key] = int(value)
        elif key in {"basemap", "base"}:
            params["basemap"] = value
        else:
            raise WeatherNext3Error(f"Неизвестный параметр WN3: {key}")
        explicit = True
        text = text.replace(match.group(0), " ", 1)

    lead = _LEAD_RE.search(text)
    if lead:
        params["hours"] = int(lead.group("value"))
        explicit = True
        text = (text[:lead.start()] + " " + text[lead.end():]).strip()

    # Bare kind tokens are accepted after the location in direct commands.
    words = text.split()
    for index in range(len(words) - 1, -1, -1):
        candidate = words[index].lower().replace("-", "_")
        if candidate in _KIND_ALIASES or candidate in WN3_KINDS:
            params["kind"] = candidate
            words.pop(index)
            explicit = True
            break
    text = " ".join(words).strip()
    if not text:
        raise WeatherNext3Error("Не указана точка. Пример: /wn3 Москва +24")

    normalized = normalize_wn3_params(params)
    if normalized["kind"] in MAP_KINDS and "hours" in params and not any(key in params for key in ("from", "to")):
        normalized["from"] = normalized["hours"]
        normalized["to"] = normalized["hours"]
        normalized["mode"] = "single"
    return ParsedWeatherNext3Input(text, normalize_wn3_params(normalized), explicit)


def wn3_kind_title(kind: str) -> str:
    return {
        "point": "Прогноз по точке",
        "meteogram": "Метеограмма · средний прогноз",
        "ensemble": "Ансамблевая метеограмма · разброс",
        "cloudgram": "Облачность по времени",
        "precip_compare": "Сравнение вариантов осадков",
        "profile": "Вертикальный профиль",
        "aero": "Аэродиаграмма",
        "windgram": "Срок × уровень",
        **KIND_TITLES,
    }[normalize_wn3_kind(kind)]


def _finite(value: Any, *, scale: float = 1.0, offset: float = 0.0) -> float | None:
    try:
        result = float(value) * scale + offset
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _row_for_hour(rows: list[dict[str, Any]], hour: int) -> dict[str, Any]:
    matches = [row for row in rows if int(row.get('forecast_hour', -1)) == hour]
    if len(matches) != 1:
        raise WeatherNext3Error(f'WN3: запрошенный срок +{hour} не опубликован или дублирован')
    return matches[0]


def _fmt(value, suffix='', digits=1):
    return '—' if value is None or not math.isfinite(float(value)) else f'{value:.{digits}f}{suffix}'


def _point_summary(point, series, hour):
    from weathernext3_surface import surface_rows
    _row_for_hour(series.rows, hour)
    row = next(row for row in surface_rows(series) if row['lead_hour'] == hour)
    return (
        f"🛰 WeatherNext 3 · {point.label}\n"
        f"Run {series.run.init_time_utc:%Y-%m-%d %HZ} · +{hour} ч · valid {row['valid_utc']:%d.%m %H:%M UTC}\n"
        f"Точка: {point.lat:.4f}, {point.lon:.4f}\n"
        f"Сетка поверхности: {series.grid_lat}, {series.grid_lon} · 0.1°\n"
        f"T/Td: {row['temperature_grid_lat']}, {row['temperature_grid_lon']} · {row['temperature_source']}\n"
        f"T {_fmt(row['temperature_mean_c'], '°C')} · p10…p90 {_fmt(row['temperature_p10_c'])}…{_fmt(row['temperature_p90_c'], '°C')}\n"
        f"Td {_fmt(row['dewpoint_mean_c'], '°C')} · RH≈{_fmt(row['rh_from_mean_pct'], '%', 0)} (из средних T/Td)\n"
        f"Ветер {_fmt(row['wind_from_mean_vector_deg'], '°', 0)} откуда · {_fmt(row['wind_speed_mean_ms'], ' м/с')} (средняя скалярная скорость)\n"
        f"MSLP {_fmt(row['pressure_msl_hpa'], ' гПа')} · облачность {_fmt(row['cloud_total_pct'], '%', 0)}\n"
        f"Осадки за час: основные {_fmt(row['precip_native_mean_mm'], ' мм')}; IMERG {_fmt(row['precip_imerg_mean_mm'], ' мм')}; эксперимент {_fmt(row['precip_experimental_mean_mm'], ' мм')}\n"
        "Google BigQuery · готовые статистики ансамбля · модель, не наблюдение"
    )


def _progress_adapter(callback: Callable[[ProgressEvent], None] | None):
    if callback is None:
        return None

    def emit(payload: dict[str, Any]) -> None:
        callback(ProgressEvent(
            stage=str(payload.get("stage", "render")),
            message=str(payload.get("message", "")),
            current=int(payload["index"]) if payload.get("index") is not None else None,
            total=int(payload["total"]) if payload.get("total") is not None else None,
            data=dict(payload),
        ))
    return emit


def build_weathernext3_product_result(
    point: Any,
    kind: str = "point",
    *,
    hours: int = 24,
    days: int = 5,
    from_: int = 1,
    to: int = 48,
    step: int = 3,
    radius: float = 150.0,
    mode: str = "animation",
    basemap: str = "places",
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    provider: WeatherNext3Provider | None = None,
    upper_provider: Any | None = None,
    **extra: Any,
) -> CommonProductResult:
    if "from" in extra:
        from_ = int(extra["from"])
    params = normalize_wn3_params({
        "kind": kind, "hours": hours, "days": days, "from": from_, "to": to,
        "step": step, "radius": radius, "mode": mode, "basemap": basemap,
        **{key: value for key, value in extra.items() if key in DEFAULT_WN3_PARAMS},
    })
    kind = params["kind"]

    from weathernext3_products import upper_product, csv_attachment, surface_plot
    from weathernext3_surface import surface_rows
    repeat = f"/wn3 {float(point.lat):.4f} {float(point.lon):.4f} " + ' '.join(f'{key}={value}' for key, value in params.items())
    if kind in UPPER_KINDS:
        result = upper_product(point, kind, params, provider=upper_provider, progress_callback=progress_callback)
        result.repeat_command = repeat
        return result
    if kind in {'meteogram', 'ensemble'}:
        from weathernext3_meteogram import fetch_weathernext3_meteogram
        def progress(text):
            if progress_callback:
                progress_callback(ProgressEvent('fetch', text))
        series = fetch_weathernext3_meteogram(point.label, point.lat, point.lon, params['days'], progress, provider=provider,
            view='mean' if kind == 'meteogram' else 'ensemble')
        result = build_meteogram_product_result(point, series.source.source_id, params['days'], params['format'],
                    progress_callback=progress_callback, series=series)
        try:
            export = [{'model': 'WeatherNext 3', 'run_utc': series.init_time_utc.isoformat(), 'valid_utc': valid,
                       **{name: values[i] for name, values in series.fields.items()}}
                      for i, valid in enumerate(series.times)]
            result.attachments.append(csv_attachment(export, 'meteogram'))
        except BaseException:
            from messenger.profile_service import cleanup_product_result
            cleanup_product_result(result)
            raise
        result.product = 'weathernext3'
        result.metadata['kind'] = kind
        result.metadata['statistical_view'] = 'mean' if kind == 'meteogram' else 'ensemble'
        result.repeat_command = repeat
        return result
    provider = provider or provider_from_env()
    if kind in {'point', 'cloudgram', 'precip_compare'}:
        if progress_callback:
            progress_callback(ProgressEvent('check', 'Выбираю опубликованный цикл и точные сроки WN3'))
        hours_to = params['hours'] if kind == 'point' else params['days'] * 24
        series = provider.point_series(point.label, point.lat, point.lon, hours_to)
        attachments = []
        try:
            if kind == 'point':
                summary = _point_summary(point, series, params['hours'])
            else:
                path = surface_plot(point, series, kind)
                attachments.append(ProductAttachment('image', path, path.name, wn3_kind_title(kind) + ' · WeatherNext 3', 'image/png'))
                rows = surface_rows(series)
                summary = (f"WeatherNext 3 · {wn3_kind_title(kind)} · {point.label}\n"
                           f"Run {series.run.init_time_utc:%Y-%m-%d %HZ} · +1…+{hours_to} ч\n"
                           f"valid {rows[0]['valid_utc']:%d.%m %H:%M} — {rows[-1]['valid_utc']:%d.%m %H:%M UTC}\n"
                           f"Точка {point.lat:.4f}, {point.lon:.4f}; сетка 0.1° {series.grid_lat}, {series.grid_lon}\n"
                           "Google BigQuery · готовое среднее ансамбля · модель, не наблюдение")
            attachments.append(csv_attachment(surface_rows(series), kind))
            return CommonProductResult('weathernext3', summary, attachments,
                {'kind': kind, 'model': 'WeatherNext 3', 'provider': 'Google BigQuery', 'data_kind': 'model',
                 'run': series.run.init_time_utc.isoformat(), 'lead': params['hours'] if kind == 'point' else None,
                 'lead_from': 1, 'lead_to': hours_to, 'grid_lat': series.grid_lat, 'grid_lon': series.grid_lon,
                 'nominal_members': 64, 'member_count': None, 'warnings': getattr(series, 'warnings', [])}, repeat)
        except BaseException:
            for attachment in attachments:
                attachment.path.unlink(missing_ok=True)
            raise

    leads = _map_leads(params)
    if progress_callback:
        progress_callback(ProgressEvent("check", "Ищу опубликованный запуск WeatherNext 3"))
    frames = provider.map_frames(
        str(point.label), float(point.lat), float(point.lon), leads,
        radius_km=float(params["radius"]), kind=kind, statistic=params["stat"],
    )
    selected = frames[0].run
    paths: list[Path] = []
    try:
        if params["mode"] == "animation":
            if progress_callback:
                progress_callback(ProgressEvent("render", "Строю кадры WeatherNext 3"))
            path = Path(write_weathernext3_animation(
                frames,
                progress_callback=_progress_adapter(progress_callback),
                basemap=str(params["basemap"]),
            ))
            paths = [path]
            mime = "video/mp4" if path.suffix.lower() == ".mp4" else "image/gif"
            attachments = [ProductAttachment(
                "animation", path, path.name,
                f"WeatherNext 3 · {wn3_kind_title(kind)} · +{leads[0]}…+{leads[-1]} ч",
                mime,
            )]
        elif params["mode"] == "series":
            attachments = []
            for index, frame in enumerate(frames, start=1):
                if progress_callback:
                    progress_callback(ProgressEvent("render", f"PNG {index}/{len(frames)}", current=index, total=len(frames)))
                path = Path(write_weathernext3_map_png(frame, basemap=str(params["basemap"])))
                paths.append(path)
                attachments.append(ProductAttachment(
                    "image", path, path.name,
                    f"WeatherNext 3 · {wn3_kind_title(kind)} · +{frame.lead_hour} ч",
                    "image/png",
                ))
        else:
            frame = frames[0]
            if progress_callback:
                progress_callback(ProgressEvent("render", "Строю PNG WeatherNext 3"))
            path = Path(write_weathernext3_map_png(frame, basemap=str(params["basemap"])))
            paths = [path]
            attachments = [ProductAttachment(
                "image", path, path.name,
                f"WeatherNext 3 · {wn3_kind_title(kind)} · +{frame.lead_hour} ч",
                "image/png",
            )]

        csv_rows = []
        units = {'precip': 'precipitation_1h_mm', 'cloud_total': 'cloud_total_pct',
                 'cloud_low': 'cloud_low_pct', 'cloud_mid': 'cloud_mid_pct', 'cloud_high': 'cloud_high_pct',
                 'temperature': 'temperature_c', 'temperature_spread': 'temperature_p90_minus_p10_c',
                 'wind100': 'wind_100m_ms', 'solar': 'solar_mean_wm2'}
        for frame in frames:
            for row in frame.rows:
                csv_rows.append({'model': 'WeatherNext 3', 'run_utc': selected.init_time_utc.isoformat(),
                                 'valid_utc': frame.valid_time_utc.isoformat(), 'lead_hour': frame.lead_hour,
                                 'statistic': 'p90-p10' if kind == 'temperature_spread' else params['stat'],
                                 **{units.get(k, k): v for k, v in row.items() if k not in {'forecast_time', 'forecast_hour'}}})
        csv = csv_attachment(csv_rows, kind)
        paths.append(csv.path)
        attachments.append(csv)
        stat_text = 'p90−p10' if kind == 'temperature_spread' else params['stat']
        first_valid, last_valid = frames[0].valid_time_utc, frames[-1].valid_time_utc
        period = f"+{leads[0]} ч" if len(leads) == 1 else f"+{leads[0]}…+{leads[-1]} ч · шаг {params['step']} ч"
        precip_note = {
            "precip_native": "model-native precipitation head",
            "precip_imerg": "IMERG-target precipitation head",
            "precip_experimental": "experimental precipitation head",
        }.get(kind, "")
        note = f"\nОсадки: {precip_note}" if precip_note else ""
        summary = (
            f"🛰 WeatherNext 3 · {wn3_kind_title(kind)}\n"
            f"Run {selected.init_time_utc:%Y-%m-%d %HZ} · {period}\n"
            f"valid {first_valid:%d.%m %H:%M} — {last_valid:%d.%m %H:%M UTC}\n"
            f"📍 {getattr(point, 'label', 'точка')} · {float(point.lat):.4f}, {float(point.lon):.4f}\n"
            f"Область: радиус {int(params['radius'])} км · surface grid 0.1° · {stat_text} ансамбля{note}\n"
            "WeatherNext 3 • Google BigQuery • модельный прогноз, не радар/наблюдение/спутниковый снимок"
        )
        time_part = (
            f"from={leads[0]} to={leads[-1]} step={params['step']} mode={params['mode']}"
            if len(leads) > 1 else f"from={leads[0]} mode=single"
        )
        return CommonProductResult(
            product="weathernext3",
            summary=summary,
            attachments=attachments,
            metadata={
                "product": "weathernext3", "kind": kind, "model": "WeatherNext 3",
                "provider": "Google BigQuery Analytics Hub", "data_kind": "model",
                "run": selected.init_time_utc.isoformat(), "lead_from": leads[0], "lead_to": leads[-1],
                "step": int(params["step"]), "mode": params["mode"], "radius": float(params["radius"]),
                "requested_lat": float(point.lat), "requested_lon": float(point.lon),
                "leads": leads, "frame_count": len(frames), "nominal_members": 64, "member_count": None, "statistic": stat_text,
            },
            repeat_command=repeat,
        )
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
