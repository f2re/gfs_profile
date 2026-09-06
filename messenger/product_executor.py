from __future__ import annotations

"""Execute immutable product snapshots through the common meteorological services."""

from dataclasses import dataclass
from typing import Any, Mapping

from geocode import GeoPoint

from .aero_service import build_aero_product_result
from .cloudgram_service import build_cloudgram_product_result
from .contracts import CommonProductResult, ProgressEvent
from .map_service import build_map_product_result
from .meteogram_service import build_meteogram_product_result
from .profile_service import build_profile_product
from .route_service import build_route_product_result
from .windgram_service import build_windgram_product_result

SUPPORTED_PRODUCTS = ("profile", "aero", "windgram", "cloudgram", "map", "meteogram", "route")


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    product: str
    point: dict[str, Any] | None
    params: dict[str, Any]

    @classmethod
    def from_values(
        cls,
        product: str,
        point: Mapping[str, Any] | Any | None,
        params: Mapping[str, Any] | None,
    ) -> "ProductSnapshot":
        product = str(product).lower().strip()
        if product not in SUPPORTED_PRODUCTS:
            raise ValueError(f"Неизвестный продукт: {product}")
        return cls(product, _pack_point(point), _clean_params(dict(params or {})))


def _pack_point(point: Mapping[str, Any] | Any | None) -> dict[str, Any] | None:
    if point is None:
        return None
    if isinstance(point, Mapping):
        lat, lon = float(point["lat"]), float(point["lon"])
        label = str(point.get("label") or f"{lat:.4f}, {lon:.4f}")
        source = str(point.get("source") or "schedule")
    else:
        lat, lon = float(point.lat), float(point.lon)
        label = str(getattr(point, "label", None) or f"{lat:.4f}, {lon:.4f}")
        source = str(getattr(point, "source", "schedule"))
    return {"lat": lat, "lon": lon, "label": label[:200], "source": source[:40]}


def _clean_params(value: dict[str, Any]) -> dict[str, Any]:
    transient = {"run", "cycle", "run_date", "run_cycle", "message_id", "status_message_id", "callback_id", "candidates", "step", "_schedule_setup"}
    result: dict[str, Any] = {}
    for key, item in value.items():
        key = str(key)
        if key in transient:
            continue
        if isinstance(item, Mapping):
            result[key] = _clean_params(dict(item))
        elif isinstance(item, (list, tuple)):
            result[key] = list(item)
        elif item is None or isinstance(item, (str, int, float, bool)):
            result[key] = item
        elif hasattr(item, "lat") and hasattr(item, "lon"):
            result[key] = _pack_point(item)
        else:
            result[key] = str(item)
    return result


def _point(value: dict[str, Any] | None) -> GeoPoint:
    if not value:
        raise ValueError("В snapshot отсутствует точка")
    return GeoPoint(float(value["lat"]), float(value["lon"]), str(value.get("label", "точка")), str(value.get("source", "schedule")))


def build_snapshot_result(
    snapshot: ProductSnapshot,
    *,
    progress_callback=None,
) -> CommonProductResult:
    """Blocking common executor. It never uses a stored run/cycle."""

    product = snapshot.product
    params = snapshot.params
    if product == "route":
        origin_raw = params.get("origin")
        destination_raw = params.get("destination")
        if not isinstance(origin_raw, dict) or not isinstance(destination_raw, dict):
            raise ValueError("В route snapshot отсутствуют origin/destination")
        origin, destination = _point(origin_raw), _point(destination_raw)
        return build_route_product_result(
            origin,
            destination,
            int(params.get("lead", 24)),
            int(params.get("speed", 300)),
            str(params.get("mode", "simple")),
            int(params.get("spatial_step", 50)),
            None,
            progress_callback=progress_callback,
        )

    point = _point(snapshot.point)
    if product == "profile":
        return build_profile_product(point, int(params.get("lead", 24)), None, progress_callback=progress_callback)
    if product == "aero":
        return build_aero_product_result(point, int(params.get("lead", 24)), None, progress_callback=progress_callback)
    if product == "windgram":
        return build_windgram_product_result(
            point,
            int(params.get("from", 0)),
            int(params.get("to", 120)),
            int(params.get("step", params.get("time_step", 6))),
            int(params.get("top", 500)),
            str(params.get("param", "wind")),
            None,
            progress_callback=progress_callback,
        )
    if product == "cloudgram":
        return build_cloudgram_product_result(
            point,
            int(params.get("from", 0)),
            int(params.get("to", 72)),
            int(params.get("step", params.get("time_step", 3))),
            str(params.get("mode", "pro")),
            None,
            progress_callback=progress_callback,
        )
    if product == "map":
        mode = str(params.get("mode", "gif"))
        lead = int(params.get("lead", 24))
        lead_from = int(params.get("from", lead if mode == "single" else 0))
        lead_to = int(params.get("to", lead if mode == "single" else 48))
        return build_map_product_result(
            point,
            lead_from,
            lead_to,
            int(params.get("step", params.get("time_step", 3))),
            mode,
            float(params.get("radius", 100)),
            str(params.get("basemap", "places")),
            None,
            progress_callback=progress_callback,
        )
    if product == "meteogram":
        return build_meteogram_product_result(
            point,
            str(params.get("source", params.get("source_id", "gfs"))),
            int(params.get("days", 5)),
            str(params.get("format", params.get("output_format", "png"))),
            progress_callback=progress_callback,
        )
    raise ValueError(f"Неизвестный продукт: {product}")
