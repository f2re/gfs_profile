from __future__ import annotations

import importlib
import sys

import docx
import meteogram_report
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
    "aero_single_mode",
    "admin_product_policy",
    "windgram_product",
    "windgram_plot",
    "cloudgram_product",
    "cloudgram_plot",
    "weather_diagnostics",
    "geocode",
    "dadata_geocoder",
    "geocode_choices",
    "geocoder_preflight",
    "route_profile",
    "route_profile_plot",
    "route_profile_contract",
    "route_profile_vertical_policy",
    "route_profile_visual_style",
    "route_profile_smoothing",
    "route_profile_icons",
    "route_profile_render_common",
    "route_profile_card_policy",
    "route_profile_render_simple",
    "route_profile_render_professional",
    "route_profile_rendering",
    "basemap_cache",
    "prepare_basemap_cache",
    "debug_map_overlay",
    "user_location_session",
    "composite_map",
    "map_animation_overlay",
    "map_animation",
    "meteogram_models",
    "meteogram_parse",
    "meteogram_fetch",
    "meteogram_data",
    "meteogram_core",
    "meteogram_plot_common",
    "meteogram_diagnostics",
    "meteogram_plot_thermo",
    "meteogram_plot_weather",
    "meteogram_plot",
    "meteogram_request",
    "telegram_commands",
    "telegram_ui",
    "telegram_product_wizard",
    "telegram_aero",
    "telegram_windgram",
    "telegram_cloudgram",
    "telegram_map",
    "telegram_route",
    "telegram_meteogram",
    "telegram_schedules",
    "telegram_bot_core",
    "telegram_concise_ux",
    "telegram_result_copy",
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
    errors = check_modules(REQUIRED_MODULES)
    if errors:
        _print_errors(errors)
        return 1

    # Plot modules import pyplot. Select the non-interactive backend before
    # importing product modules so runtime_check also works under systemd/SSH.
    import matplotlib

    matplotlib.use("Agg", force=True)
    errors = check_modules(OPTIONAL_RUNTIME_MODULES)
    if errors:
        _print_errors(errors)
        return 1

    print(
        "Runtime check OK: GFS/GRIB, DaData, Skew-T, route, "
        "model/ensemble meteograms and Telegram UX import successfully"
    )
    return 0


def _print_errors(errors: list[str]) -> None:
    print("Runtime check failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
