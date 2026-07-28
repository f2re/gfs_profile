from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from basemap_cache import check_basemap_cache, local_basemap_overlay
from composite_map import MAP_BASEMAPS, MAP_RADIUS_KM, area_box_from_radius
from geocode import GeoPoint, resolve_location


def _point_from_args(args: argparse.Namespace) -> GeoPoint:
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise SystemExit("--lat and --lon must be provided together")
        return GeoPoint(float(args.lat), float(args.lon), f"{float(args.lat):.4f}, {float(args.lon):.4f}", "coordinates")
    if not args.location:
        raise SystemExit("Provide a location or --lat/--lon")
    return resolve_location(args.location)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose local Natural Earth basemap overlay for /map.")
    parser.add_argument("location", nargs="?", help="Location name, for example Москва or Краснодар")
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--basemap", choices=MAP_BASEMAPS, default="places", help="Basemap mode")
    parser.add_argument("--radius-km", type=float, default=MAP_RADIUS_KM, help="Map radius in km")
    parser.add_argument("--resolution", default=None, help="Natural Earth resolution: 10m, 50m or 110m")
    parser.add_argument("--json", action="store_true", help="Print full diagnostic payload as JSON")
    return parser


def diagnostic_payload(args: argparse.Namespace) -> dict:
    point = _point_from_args(args)
    bbox = area_box_from_radius(point.lat, point.lon, float(args.radius_km))
    cache = check_basemap_cache(args.resolution)
    overlay = local_basemap_overlay(point.lat, point.lon, float(args.radius_km), args.basemap, args.resolution)
    stats = overlay.get("stats") or {}
    return {
        "point": {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source},
        "bbox": bbox,
        "basemap": args.basemap,
        "resolution": cache.resolution,
        "data_dir": str(cache.data_dir),
        "manifest": str(cache.manifest_path),
        "cache_ready": cache.ready,
        "missing_layers": cache.missing_layers,
        "failed_layers": cache.failed_layers,
        "layer_status": {name: {"ok": status.ok, "path": str(status.path) if status.path else None, "error": status.error} for name, status in cache.layers.items()},
        "overlay_status": stats.get("status", "unknown"),
        "overlay_warnings": stats.get("warnings") or [],
        "counts": {
            "coastline": stats.get("coastline_count", 0),
            "water": stats.get("water_count", 0),
            "rivers": stats.get("river_count", 0),
            "admin": stats.get("admin_count", 0),
            "roads": stats.get("road_count", 0),
            "cities": stats.get("city_count", 0),
        },
    }


def print_text(payload: dict) -> None:
    print(f"point: {payload['point']['label']} ({payload['point']['lat']:.4f}, {payload['point']['lon']:.4f}) source={payload['point']['source']}")
    print(f"bbox: {payload['bbox']}")
    print(f"basemap mode: {payload['basemap']}")
    print(f"resolution: {payload['resolution']}")
    print(f"data dir: {Path(payload['data_dir'])}")
    print(f"manifest: {Path(payload['manifest'])}")
    print(f"cache ready: {'yes' if payload['cache_ready'] else 'no'}")
    if payload["missing_layers"]:
        print("missing layers: " + ", ".join(payload["missing_layers"]))
    if payload["failed_layers"]:
        print("failed layers:")
        for layer, error in payload["failed_layers"].items():
            print(f"  - {layer}: {error}")
    counts = payload["counts"]
    print(
        "parsed coastline/water/river/admin/road/city counts: "
        f"{counts['coastline']}/{counts['water']}/{counts['rivers']}/{counts['admin']}/{counts['roads']}/{counts['cities']}"
    )
    if payload["overlay_warnings"]:
        print("warnings: " + "; ".join(payload["overlay_warnings"]))
    print(f"final status: {payload['overlay_status']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = diagnostic_payload(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_text(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
