from __future__ import annotations

import importlib
import sys

REQUIRED_MODULES = (
    "numpy",
    "pandas",
    "requests",
    "xarray",
    "cfgrib",
    "eccodes",
    "telegram",
    "matplotlib",
    "shapefile",
    "PIL",
    "metpy",
    "scipy",
    "pint",
)

OPTIONAL_RUNTIME_MODULES = (
    "plot_style",
    "aviation_style",
    "profile_plot_ru",
    "gfs_subset",
    "aero_plot",
    "aero_product",
    "windgram_product",
    "windgram_plot",
    "cloudgram_product",
    "cloudgram_plot",
    "weather_diagnostics",
    "route_profile",
    "route_profile_plot",
    "route_profile_contract",
    "route_profile_simple_overlay",
    "basemap_cache",
    "prepare_basemap_cache",
    "debug_map_overlay",
    "user_location_session",
    "composite_map",
    "map_animation_overlay",
    "map_animation",
    "telegram_commands",
    "telegram_product_wizard",
    "telegram_aero",
    "telegram_windgram",
    "telegram_cloudgram",
    "telegram_map",
    "telegram_route",
    "telegram_bot_core",
    "telegram_bot",
)


def check_modules(modules: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    return errors


def main() -> int:
    errors = check_modules(REQUIRED_MODULES + OPTIONAL_RUNTIME_MODULES)
    if errors:
        print("Runtime check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    import matplotlib

    matplotlib.use("Agg", force=True)
    print("Runtime check OK: dependencies, route risk contract, command definitions, route/map/cloudgram, wizard and bot modules import successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
