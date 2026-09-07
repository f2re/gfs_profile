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

WN3_KINDS = ("point", "meteogram", *MAP_KINDS)
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
}

_KIND_ALIASES = {
    "forecast": "point", "прогноз": "point", "точка": "point",
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
    if not 1 <= result["hours"] <= 360:
        raise WeatherNext3Error("hours должен быть 1..360")
    if not 1 <= result["days"] <= 15:
        raise WeatherNext3Error("days должен быть 1..15")
    if not 25 <= result["radius"] <= 500:
        raise WeatherNext3Error("radius должен быть 25..500 км")
    if result["kind"] in MAP_KINDS:
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
        elif key in {"mode", "format"}:
            params["mode"] = value
        elif key in {"basemap", "base"}:
            params["basemap"] = value
        else:
            continue
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
        "meteogram": "Ансамблевая метеограмма",
        **KIND_TITLES,
    }[normalize_wn3_kind(kind)]


def _finite(value: Any, *, scale: float = 1.0, offset: float = 0.0) -> float | None:
    try:
        result = float(value) * scale + offset
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _row_for_hour(rows: list[dict[str, Any]], hour: int) -> dict[str, Any]:
    if not rows:
        raise WeatherNext3Error("WeatherNext 3 не вернул точечный прогноз")
    return min(rows, key=lambda row: abs(int(row.get("forecast_hour", hour)) - hour))


def _wind_from(u: float | None, v: float | None) -> tuple[float | None, float | None]:
    if u is None or v is None:
        return None, None
    speed = math.hypot(u, v)
    direction = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    return direction, speed


def _rh(temp: float | None, dew: float | None) -> float | None:
    if temp is None or dew is None:
        return None
    try:
        value = 100.0 * math.exp((17.625 * dew) / (243.04 + dew) - (17.625 * temp) / (243.04 + temp))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return max(0.0, min(100.0, value))


def _fmt(value: float | None, suffix: str, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _point_summary(point: Any, series: Any, hour: int) -> str:
    row = _row_for_hour(series.rows, hour)
    actual_hour = int(row.get("forecast_hour", hour))
    valid = row.get("forecast_time")
    if not isinstance(valid, datetime):
        valid = datetime.fromisoformat(str(valid).replace("Z", "+00:00"))
    if valid.tzinfo is None:
        valid = valid.replace(tzinfo=timezone.utc)
    valid = valid.astimezone(timezone.utc)

    temp = _finite(row.get("station_temperature_mean"), offset=-273.15)
    dew = _finite(row.get("station_dewpoint_mean"), offset=-273.15)
    if temp is None:
        temp = _finite(row.get("temperature_mean"), offset=-273.15)
    if dew is None:
        dew = _finite(row.get("dewpoint_mean"), offset=-273.15)
    t10 = _finite(row.get("station_temperature_p10"), offset=-273.15)
    t90 = _finite(row.get("station_temperature_p90"), offset=-273.15)
    if t10 is None:
        t10 = _finite(row.get("temperature_p10"), offset=-273.15)
    if t90 is None:
        t90 = _finite(row.get("temperature_p90"), offset=-273.15)
    pressure = _finite(row.get("pressure_mean"), scale=0.01)
    cloud = _finite(row.get("cloud_total_mean"), scale=100.0)
    u, v = _finite(row.get("wind_u_mean")), _finite(row.get("wind_v_mean"))
    direction, speed = _wind_from(u, v)
    native = _finite(row.get("precip_native_mean"), scale=1000.0)
    imerg = _finite(row.get("precip_imerg_mean"), scale=1000.0)
    experimental = _finite(row.get("precip_experimental_mean"), scale=1000.0)
    station_note = "station head 0.05°" if row.get("station_temperature_mean") is not None else "grid 0.1°"
    grid_lat = series.station_grid_lat if series.station_grid_lat is not None else series.grid_lat
    grid_lon = series.station_grid_lon if series.station_grid_lon is not None else series.grid_lon
    grid_line = ""
    if grid_lat is not None and grid_lon is not None:
        grid_line = f"\n📐 WN3 grid: {float(grid_lat):.4f}, {float(grid_lon):.4f} · {station_note}"
    spread = ""
    if t10 is not None and t90 is not None:
        spread = f" · p10…p90 {_fmt(t10, '°C')}…{_fmt(t90, '°C')}"
    wind = "—" if speed is None or direction is None else f"{direction:.0f}° откуда · {speed:.1f} м/с"
    return (
        "🛰 WeatherNext 3 · прогноз по точке\n"
        f"Run {series.run.init_time_utc:%Y-%m-%d %HZ} · +{actual_hour} ч · valid {valid:%d.%m %H:%M UTC}\n"
        f"📍 {getattr(point, 'label', 'точка')} · {float(point.lat):.4f}, {float(point.lon):.4f}{grid_line}\n"
        f"🌡 T {_fmt(temp, '°C')}{spread} · Td {_fmt(dew, '°C')} · RH {_fmt(_rh(temp, dew), '%', 0)}\n"
        f"💨 {wind} · MSLP {_fmt(pressure, ' гПа', 0)} · ☁ {_fmt(cloud, '%', 0)}\n"
        f"🌧 1 ч: native {_fmt(native, ' мм')} · IMERG {_fmt(imerg, ' мм')} · experimental {_fmt(experimental, ' мм')}\n"
        "64-членный ансамбль · показаны готовые статистики BigQuery\n"
        "WeatherNext 3 • Google • модельный прогноз, не наблюдение/радар/спутниковый снимок"
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
    **extra: Any,
) -> CommonProductResult:
    if "from" in extra:
        from_ = int(extra["from"])
    params = normalize_wn3_params({
        "kind": kind, "hours": hours, "days": days, "from": from_, "to": to,
        "step": step, "radius": radius, "mode": mode, "basemap": basemap,
    })
    kind = params["kind"]

    if kind == "meteogram":
        if progress_callback:
            progress_callback(ProgressEvent("fetch", "Получаю ансамблевую метеограмму WeatherNext 3"))
        result = build_meteogram_product_result(
            point,
            "weathernext3",
            int(params["days"]),
            "png",
            progress_callback=progress_callback,
        )
        result.product = "weathernext3"
        result.metadata.update({"kind": "meteogram", "product": "weathernext3"})
        result.repeat_command = f"/wn3 {float(point.lat):.4f} {float(point.lon):.4f} kind=meteogram days={params['days']}"
        return result

    provider = provider or provider_from_env()
    if kind == "point":
        if progress_callback:
            progress_callback(ProgressEvent("check", "Ищу опубликованный запуск WeatherNext 3"))
        series = provider.point_series(str(point.label), float(point.lat), float(point.lon), int(params["hours"]))
        if progress_callback:
            progress_callback(ProgressEvent("format", "Формирую точечный прогноз"))
        return CommonProductResult(
            product="weathernext3",
            summary=_point_summary(point, series, int(params["hours"])),
            attachments=[],
            metadata={
                "product": "weathernext3", "kind": "point", "model": "WeatherNext 3",
                "provider": "Google BigQuery Analytics Hub", "data_kind": "model",
                "run": series.run.init_time_utc.isoformat(), "lead": int(params["hours"]),
                "requested_lat": float(point.lat), "requested_lon": float(point.lon),
                "grid_lat": series.station_grid_lat if series.station_grid_lat is not None else series.grid_lat,
                "grid_lon": series.station_grid_lon if series.station_grid_lon is not None else series.grid_lon,
                "ensemble_members": 64,
            },
            repeat_command=f"/wn3 {float(point.lat):.4f} {float(point.lon):.4f} +{params['hours']}",
        )

    leads = _map_leads(params)
    if progress_callback:
        progress_callback(ProgressEvent("check", "Ищу опубликованный запуск WeatherNext 3"))
    frames = provider.map_frames(
        str(point.label), float(point.lat), float(point.lon), leads,
        radius_km=float(params["radius"]), kind=kind,
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
            f"Область: радиус {int(params['radius'])} км · surface grid 0.1° · 64-member mean{note}\n"
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
                "frame_count": len(frames), "ensemble_members": 64,
            },
            repeat_command=(
                f"/wn3 {float(point.lat):.4f} {float(point.lon):.4f} kind={kind} {time_part} "
                f"radius={int(params['radius'])} basemap={params['basemap']}"
            ),
        )
    except Exception:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
