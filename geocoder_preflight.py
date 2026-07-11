from __future__ import annotations

import argparse
import os
import sys

from dadata_geocoder import dadata_api_key, validate_dadata_access
from geocode import GeocodeError
from geocode_choices import configured_geocoder_providers


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка конфигурации геокодеров")
    parser.add_argument("--no-network", action="store_true", help="проверить только переменные окружения")
    args = parser.parse_args()

    try:
        providers = configured_geocoder_providers()
    except GeocodeError as exc:
        print(f"Geocoder preflight failed: {exc}", file=sys.stderr)
        return 1

    print(f"Geocoder providers: {','.join(providers)}")
    if "dadata" not in providers:
        print("DaData disabled by GEOCODER_PROVIDERS")
        return 0

    if not dadata_api_key():
        print("Geocoder preflight failed: DADATA_API_KEY is required", file=sys.stderr)
        return 2

    if args.no_network:
        print("DaData configuration OK: API key is set")
        return 0

    try:
        point = validate_dadata_access()
    except GeocodeError as exc:
        print(f"Geocoder preflight failed: {exc}", file=sys.stderr)
        return 3

    print(f"DaData OK: Москва -> {point.lat:.4f}, {point.lon:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
