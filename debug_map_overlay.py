from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from composite_map import MAP_BASEMAPS, MAP_RADIUS_KM, _overlay_cache_path, area_box_from_radius, load_basemap_outlines, load_overlay, summarize_overlay
from geocode import GeoPoint, resolve_location


def _point_from_args(args: argparse.Namespace) -> GeoPoint:
    if args.lat is not None or args.lon is not None:
        if args.lat is None or args.lon is None:
            raise SystemExit("--lat and --lon must be provided together")
        return GeoPoint(float(args.lat), float(args.lon), f"{float(args.lat):.4f}, {float(args.lon):.4f}", "coordinates")
    if not args.location:
        raise SystemExit("Provide a location or --lat/--lon")
    return resolve_location(args.location)


def _format_attempt(attempt: dict) -> str:
    parts = [
        f"endpoint={attempt.get('endpoint') or '-'}",
        f"http={attempt.get('http_status') if attempt.get('http_status') is not None else '-'}",
        f"elapsed_ms={attempt.get('elapsed_ms') if attempt.get('elapsed_ms') is not None else '-'}",
        f"bytes={attempt.get('response_bytes') if attempt.get('response_bytes') is not None else '-'}",
        f"elements={attempt.get('elements') if attempt.get('elements') is not None else '-'}",
    ]
    if attempt.get("error_type") or attempt.get("error"):
        parts.append(f"error={attempt.get('error_type') or 'Error'}: {attempt.get('error') or ''}")
    return "  - " + ", ".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose OSM/Overpass overlay loading for /map.")
    parser.add_argument("location", nargs="?", help="Location name, for example Москва or Краснодар")
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--basemap", choices=MAP_BASEMAPS, default="places", help="Overlay mode")
    parser.add_argument("--radius-km", type=float, default=MAP_RADIUS_KM, help="Map radius in km")
    parser.add_argument("--json", action="store_true", help="Print full diagnostic payload as JSON")
    return parser


def diagnostic_payload(args: argparse.Namespace) -> dict:
    point = _point_from_args(args)
    bbox = area_box_from_radius(point.lat, point.lon, float(args.radius_km))
    cache_path = _overlay_cache_path(bbox, args.basemap)
    cache_existed_before = cache_path.exists()
    overlay = load_overlay(bbox, args.basemap)
    outlines = load_basemap_outlines(bbox) if args.basemap != "basic" else {"_meta": {"status": "disabled"}}
    meta = overlay.get("_meta") or {}
    outline_meta = outlines.get("_meta") or {}
    stats = summarize_overlay(overlay, point.lat, point.lon)
    parsed_counts = {
        "cities": stats.get("city_count", 0),
        "water": stats.get("water_count", 0),
        "rivers": stats.get("river_count", 0),
        "roads": stats.get("road_count", 0),
    }
    return {
        "point": {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source},
        "bbox": bbox,
        "basemap": args.basemap,
        "cache_path": str(cache_path),
        "cache_hit": bool(meta.get("cache_hit")),
        "cache_miss": not bool(meta.get("cache_hit")),
        "cache_existed_before": cache_existed_before,
        "query_hash": meta.get("query_hash"),
        "endpoint_attempts": meta.get("attempts") or [],
        "http_status": meta.get("http_status"),
        "response_bytes": meta.get("response_bytes"),
        "raw_elements_count": len(overlay.get("elements") or []),
        "element_counts": meta.get("element_counts") or {},
        "parsed_counts": parsed_counts,
        "outline_status": outline_meta.get("status", "unknown"),
        "outline_resolution": outline_meta.get("resolution"),
        "outline_cache_hit": bool(outline_meta.get("cache_hit")),
        "outline_feature_counts": outline_meta.get("feature_counts") or {},
        "outline_line_count": int(outline_meta.get("line_count") or 0),
        "outline_point_count": int(outline_meta.get("point_count") or 0),
        "outline_error": outline_meta.get("error"),
        "final_status": meta.get("status", "unknown"),
        "error": meta.get("error"),
    }


def print_text(payload: dict) -> None:
    print(f"point: {payload['point']['label']} ({payload['point']['lat']:.4f}, {payload['point']['lon']:.4f}) source={payload['point']['source']}")
    print(f"bbox: {payload['bbox']}")
    print(f"basemap mode: {payload['basemap']}")
    print(f"cache path: {Path(payload['cache_path'])}")
    print(f"cache hit / miss: {'hit' if payload['cache_hit'] else 'miss'} (existed_before={payload['cache_existed_before']})")
    print(f"query hash: {payload.get('query_hash') or '-'}")
    print("endpoint attempts:")
    attempts = payload.get("endpoint_attempts") or []
    if attempts:
        for attempt in attempts:
            print(_format_attempt(attempt))
    else:
        print("  - none")
    print(f"HTTP status: {payload.get('http_status') if payload.get('http_status') is not None else '-'}")
    print(f"response bytes: {payload.get('response_bytes') if payload.get('response_bytes') is not None else '-'}")
    print(f"raw elements count: {payload['raw_elements_count']}")
    counts = payload["parsed_counts"]
    print(f"parsed city/water/river/road counts: {counts['cities']}/{counts['water']}/{counts['rivers']}/{counts['roads']}")
    print(
        "basemap outlines: "
        f"status={payload['outline_status']}, resolution={payload.get('outline_resolution') or '-'}, "
        f"cache_hit={payload['outline_cache_hit']}, lines={payload['outline_line_count']}, "
        f"points={payload['outline_point_count']}, features={payload['outline_feature_counts']}"
    )
    if payload.get("outline_error"):
        print(f"outline error: {payload['outline_error']}")
    if payload.get("error"):
        print(f"error: {payload['error']}")
    print(f"final status: {payload['final_status']}")


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
