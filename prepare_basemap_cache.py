from __future__ import annotations

import argparse
import sys

from basemap_cache import LAYER_SPECS, basemap_resolution, check_basemap_cache, ensure_basemap_cache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare offline Natural Earth basemap cache for /map.")
    parser.add_argument("--resolution", default=None, help="Natural Earth resolution: 10m, 50m or 110m")
    parser.add_argument("--force", action="store_true", help="Download all layers again")
    parser.add_argument("--check", action="store_true", help="Only check local cache; do not download")
    return parser


def _print_status(status) -> None:
    print(f"resolution: {status.resolution}")
    print(f"data dir: {status.data_dir}")
    print(f"manifest: {status.manifest_path}")
    print(f"ready: {'yes' if status.ready else 'no'}")
    print("layers:")
    for layer in LAYER_SPECS:
        item = status.layers.get(layer)
        required = "required" if LAYER_SPECS[layer].get("required") else "optional"
        if item is None:
            print(f"  - {layer}: missing ({required})")
        elif item.ok:
            suffix = "downloaded" if item.downloaded else "present"
            print(f"  - {layer}: ok ({required}, {suffix}) {item.path}")
        else:
            print(f"  - {layer}: missing ({required}) {item.error or ''}".rstrip())
    if status.failed_layers:
        print("failed layers:")
        for layer, error in status.failed_layers.items():
            print(f"  - {layer}: {error}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    resolution = basemap_resolution(args.resolution)
    status = check_basemap_cache(resolution) if args.check else ensure_basemap_cache(resolution, force=args.force)
    _print_status(status)
    return 0 if status.ready else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
