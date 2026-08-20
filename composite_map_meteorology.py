from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

import numpy as np

import composite_map as base
from geocode import GeoPoint
from gfs_core import GfsProfileError, GfsRun, ProgressCallback
from gfs_subset import GribFieldSelector, open_grib_datasets, select_grib_field
from weather_diagnostics import DASH, instant_weather_code, precipitation_code, thunder_score, visibility_km

CAPE_LAYER_PA = 18000.0
MAP_ACTIVE_PRECIP_RATE_MMH = 0.1
MAP_THUNDER_PRECIP_RATE_MMH = 0.2


def _emit(progress_callback: ProgressCallback | None, **payload) -> None:
    if progress_callback:
        progress_callback(payload)


def _selected_field(datasets, selector: GribFieldSelector):
    selected = select_grib_field(datasets, selector)
    if selected is None:
        return None
    data_array = selected.data_array
    try:
        values = np.asarray(data_array.values).squeeze().astype(float)
        while values.ndim > 2:
            values = values[0]
        lat2d, lon2d = base._coords_from_dataarray(data_array)
        if values.shape != lat2d.shape and values.T.shape == lat2d.shape:
            values = values.T
        if values.shape != lat2d.shape:
            return None
        return values, lat2d, lon2d, selected
    except Exception:
        return None


def _field(
    datasets,
    names,
    *,
    type_of_level=None,
    level=None,
    step_types=None,
    interval_hours=None,
    prefer_shortest_interval=False,
):
    return _selected_field(
        datasets,
        GribFieldSelector(
            names=tuple(names),
            type_of_level=tuple(type_of_level) if type_of_level else None,
            level=level,
            step_types=tuple(step_types) if step_types else None,
            interval_hours=interval_hours,
            prefer_shortest_interval=prefer_shortest_interval,
        ),
    )


def _cape_cin(datasets):
    cape = _field(
        datasets,
        ("cape",),
        type_of_level=("pressureFromGroundLayer",),
        level=CAPE_LAYER_PA,
        step_types=("instant",),
    )
    cin = _field(
        datasets,
        ("cin",),
        type_of_level=("pressureFromGroundLayer",),
        level=CAPE_LAYER_PA,
        step_types=("instant",),
    )
    if cape is not None and cin is not None:
        return cape, cin, "180–0 hPa AGL"
    return None, None, "unavailable"


def _bool_grid(datasets, names):
    item = _field(datasets, names, type_of_level=("surface",), step_types=("instant",))
    if item is None:
        return None
    return item[0] >= 0.5, item[1], item[2]


def _forecast_interval_hours(lead_hour: int) -> float:
    return 1.0 if int(lead_hour) <= 120 else 3.0


def _storm_grid(cape, cin, conv_precip, conv_cloud, conv_rate, conv_precip_interval_hours: float = 1.0):
    reference = next((value for value in (cape, cin, conv_precip, conv_cloud, conv_rate) if value is not None), None)
    if reference is None:
        return None
    missing = np.full_like(reference, np.nan, dtype=float)

    def score(ca, ci, cp, cc, cr):
        def optional(value):
            return None if np.isnan(value) else float(value)

        return float(
            thunder_score(
                optional(ca),
                optional(ci),
                optional(cp),
                optional(cc),
                optional(cr),
                conv_precip_interval_hours=conv_precip_interval_hours,
            )
        )

    return np.vectorize(score, otypes=[float])(
        cape if cape is not None else missing,
        cin if cin is not None else missing,
        conv_precip if conv_precip is not None else missing,
        conv_cloud if conv_cloud is not None else missing,
        conv_rate if conv_rate is not None else missing,
    )


def _phenomenon_grid(
    precip_rate_mmh,
    rain,
    snow,
    freezing,
    ice_pellets,
    convective_score,
    visibility,
    *,
    min_rate_mmh: float = MAP_ACTIVE_PRECIP_RATE_MMH,
):
    reference = next(
        (
            value
            for value in (precip_rate_mmh, rain, snow, freezing, ice_pellets, convective_score, visibility)
            if value is not None
        ),
        None,
    )
    if reference is None:
        return None
    shape = np.asarray(reference).shape
    codes = np.full(shape, DASH, dtype=object)

    def value_at(values, row, col, default=None):
        if values is None:
            return default
        return np.asarray(values)[row, col]

    for row, col in np.ndindex(shape):
        rate = value_at(precip_rate_mmh, row, col)
        rate_value = None if rate is None or not np.isfinite(float(rate)) else float(rate)
        vis = value_at(visibility, row, col)
        vis_value = None if vis is None or not np.isfinite(float(vis)) else float(vis)
        score = value_at(convective_score, row, col, 0.0)
        score_value = int(round(float(score))) if score is not None and np.isfinite(float(score)) else 0
        phase = precipitation_code(
            bool(value_at(rain, row, col, False)),
            bool(value_at(snow, row, col, False)),
            bool(value_at(freezing, row, col, False)),
            bool(value_at(ice_pellets, row, col, False)),
        )
        codes[row, col] = instant_weather_code(
            rate_value,
            phase,
            score_value,
            vis_value,
            min_rate_mmh=min_rate_mmh,
            thunder_min_rate_mmh=MAP_THUNDER_PRECIP_RATE_MMH,
        )
    return codes


def build_composite_map(
    run: GfsRun,
    lead_hour: int,
    point: GeoPoint,
    radius_km: float = base.MAP_RADIUS_KM,
    basemap: str = base.MAP_BASEMAP_DEFAULT,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    """Build the map from metadata-exact GFS fields.

    The green field is interval precipitation amount. Surface phenomenon symbols
    are a separate valid-time layer: an icon is emitted for the exact GFS grid
    cell only when PRATE reaches the measurable threshold and the instantaneous
    categorical phase fields identify the precipitation type. No accumulated
    APCP value is allowed to invent a current rain symbol.
    """

    basemap = base._validate_basemap(basemap)
    _emit(progress_callback, stage="map_cycle", message="Выбираю опубликованный цикл GFS")
    path, box = base.download_area_subset(
        run.date,
        run.cycle,
        lead_hour,
        point.lat,
        point.lon,
        radius_km,
        progress_callback,
    )
    _emit(progress_callback, stage="map_parse", message="Читаю GRIB2 и проверяю уровни/интервалы")
    missing: set[str] = set()
    expected_interval = _forecast_interval_hours(lead_hour)

    with tempfile.TemporaryDirectory() as tmp:
        datasets = open_grib_datasets(path, Path(tmp))
        cloud_item = _field(
            datasets,
            ("tcc", "tcdc"),
            type_of_level=("atmosphere", "entireAtmosphere"),
            step_types=("instant",),
        )
        apcp_item = _field(
            datasets,
            ("tp", "apcp"),
            type_of_level=("surface",),
            step_types=("accum",),
            interval_hours=expected_interval,
        )
        prate_item = _field(
            datasets,
            ("prate",),
            type_of_level=("surface",),
            step_types=("instant",),
        )
        u_item = _field(
            datasets,
            ("u", "ugrd"),
            type_of_level=("isobaricInhPa", "isobaricInPa"),
            level=500.0,
            step_types=("instant",),
        )
        v_item = _field(
            datasets,
            ("v", "vgrd"),
            type_of_level=("isobaricInhPa", "isobaricInPa"),
            level=500.0,
            step_types=("instant",),
        )
        base_item = cloud_item or apcp_item or prate_item or u_item
        if base_item is None:
            raise GfsProfileError("В GRIB2 карты не найдены пригодные строго идентифицированные 2D-поля")

        _, lat2d, lon2d, _ = base_item
        x, y, dist = base._xy_km(lat2d, lon2d, point.lat, point.lon)
        radius_mask = dist <= radius_km

        conv_cloud_item = _field(
            datasets,
            ("tcc", "tcdc"),
            type_of_level=("convectiveCloudLayer",),
            step_types=("instant",),
        )
        acpcp_item = _field(
            datasets,
            ("acpcp",),
            type_of_level=("surface",),
            step_types=("accum",),
            interval_hours=expected_interval,
        )
        cprat_item = _field(
            datasets,
            ("cprat",),
            type_of_level=("surface",),
            step_types=("instant",),
        )
        cape_item, cin_item, cape_layer = _cape_cin(datasets)
        vis_item = _field(
            datasets,
            ("vis", "visibility"),
            type_of_level=("surface",),
            step_types=("instant",),
        )

        precip_interval = expected_interval
        precip_source = "none"
        if apcp_item is not None:
            precip = np.maximum(0.0, apcp_item[0])
            precip_interval = float(apcp_item[3].interval_hours or expected_interval)
            precip_source = "APCP accumulation"
        elif prate_item is not None:
            precip = np.maximum(0.0, prate_item[0] * 3600.0 * expected_interval)
            precip_source = "PRATE integrated"
        else:
            precip = None

        precip_rate_mmh = np.maximum(0.0, prate_item[0] * 3600.0) if prate_item is not None else None
        cloud = np.clip(cloud_item[0], 0.0, 100.0) if cloud_item is not None else None
        conv_cloud = np.clip(conv_cloud_item[0], 0.0, 100.0) if conv_cloud_item is not None else None
        conv_precip = np.maximum(0.0, acpcp_item[0]) if acpcp_item is not None else None
        conv_interval = float(acpcp_item[3].interval_hours or expected_interval) if acpcp_item is not None else expected_interval
        conv_rate = np.maximum(0.0, cprat_item[0] * 3600.0) if cprat_item is not None else None
        cape = cape_item[0] if cape_item is not None else None
        cin = cin_item[0] if cin_item is not None else None
        visibility = np.vectorize(visibility_km, otypes=[float])(vis_item[0]) if vis_item is not None else None
        u500 = u_item[0] if u_item is not None else None
        v500 = v_item[0] if v_item is not None else None

        convective_score = _storm_grid(cape, cin, conv_precip, conv_cloud, conv_rate, conv_interval)
        rain = _bool_grid(datasets, ("crain",))
        snow = _bool_grid(datasets, ("csnow",))
        cold = _bool_grid(datasets, ("cfrzr",))
        ice = _bool_grid(datasets, ("cicep",))
        rain_grid = rain[0] if rain is not None else np.zeros_like(lat2d, dtype=bool)
        snow_grid = snow[0] if snow is not None else np.zeros_like(lat2d, dtype=bool)
        cold_grid = cold[0] if cold is not None else np.zeros_like(lat2d, dtype=bool)
        ice_grid = ice[0] if ice is not None else np.zeros_like(lat2d, dtype=bool)

        phenomenon_code = _phenomenon_grid(
            precip_rate_mmh,
            rain_grid,
            snow_grid,
            cold_grid,
            ice_grid,
            convective_score,
            visibility,
        )
        if convective_score is None or precip_rate_mmh is None:
            confirmed_storm = None
        else:
            confirmed_storm = np.where(
                (convective_score >= 3.0) & (precip_rate_mmh >= MAP_THUNDER_PRECIP_RATE_MMH),
                3.0,
                0.0,
            )

    for name, value in {
        "осадки": precip,
        "интенсивность осадков": precip_rate_mmh,
        "облачность": cloud,
        "конвективная облачность": conv_cloud,
        "гроза": confirmed_storm,
        "CAPE/CIN 180–0 hPa": cape if cape_layer == "180–0 hPa AGL" else None,
        "ветер_500": u500 if v500 is not None else None,
        "видимость": visibility,
    }.items():
        if value is None:
            missing.add(name)

    valid_time = run.run_datetime_utc + timedelta(hours=lead_hour)
    _emit(progress_callback, stage="map_done", message="Поля карты подготовлены")
    return {
        "run": run,
        "lead_hour": lead_hour,
        "valid_time": valid_time,
        "point": point,
        "radius_km": radius_km,
        "box": box,
        "basemap": basemap,
        "x": x,
        "y": y,
        "dist": dist,
        "mask": radius_mask,
        "lat": lat2d,
        "lon": lon2d,
        "precip": precip,
        "precip_interval_hours": float(precip_interval),
        "precip_source": precip_source,
        "precip_rate_mmh": precip_rate_mmh,
        "precip_rate_source": "PRATE forecast" if precip_rate_mmh is not None else "unavailable",
        "phenomenon_rate_threshold_mmh": MAP_ACTIVE_PRECIP_RATE_MMH,
        "phenomenon_code": phenomenon_code,
        "phenomenon_source": "PRATE forecast + CRAIN/CSNOW/CFRZR/CICEP forecast",
        "cloud": cloud,
        "convective_cloud": conv_cloud,
        "convective_score": convective_score,
        "convective_precip_interval_hours": float(conv_interval),
        "storm": confirmed_storm,
        "cape": cape,
        "cin": cin,
        "cape_layer": cape_layer,
        "visibility": visibility,
        "u500": u500,
        "v500": v500,
        "rain": rain_grid,
        "snow": snow_grid,
        "cold": cold_grid,
        "ice": ice_grid,
        "missing": missing,
    }


def build_composite_map_frames(
    run: GfsRun,
    leads: list[int],
    point: GeoPoint,
    radius_km: float = base.MAP_RADIUS_KM,
    basemap: str = base.MAP_BASEMAP_DEFAULT,
    progress_callback: ProgressCallback | None = None,
) -> list[dict]:
    frames: list[dict] = []
    for index, lead in enumerate(leads, start=1):
        _emit(
            progress_callback,
            stage="map_step",
            message=f"Готовлю срок +{lead} ч",
            index=index,
            total=len(leads),
            lead_hour=lead,
        )
        frames.append(
            build_composite_map(
                run,
                lead,
                point,
                radius_km=radius_km,
                basemap=basemap,
                progress_callback=progress_callback,
            )
        )
    return frames
