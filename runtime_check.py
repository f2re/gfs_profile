from __future__ import annotations

import importlib
import sys

import docx
import meteogram_report
REQUIRED_MODULES = (
    "numpy", "pandas", "requests", "xarray", "cfgrib", "eccodes", "telegram", "fastapi", "uvicorn",
    "matplotlib", "shapefile", "PIL", "metpy", "scipy", "pint",
)
OPTIONAL_RUNTIME_MODULES = (
    "plot_style", "aviation_style", "profile_plot_ru", "gfs_subset", "aero_plot", "aero_product", "aero_single_mode",
    "admin_product_policy", "windgram_product", "windgram_plot", "cloudgram_product", "cloudgram_plot", "weather_diagnostics",
    "geocode", "dadata_geocoder", "geocode_choices", "geocoder_preflight", "route_profile", "route_profile_plot",
    "route_profile_contract", "route_profile_vertical_policy", "route_profile_visual_style", "route_profile_smoothing",
    "route_profile_icons", "route_profile_render_common", "route_profile_card_policy", "route_profile_render_simple",
    "route_profile_render_professional", "route_profile_rendering", "basemap_cache", "prepare_basemap_cache", "debug_map_overlay",
    "user_location_session", "composite_map", "map_animation_overlay", "map_animation", "meteogram_models", "meteogram_parse",
    "meteogram_fetch", "meteogram_data", "meteogram_core", "meteogram_plot_common", "meteogram_diagnostics", "meteogram_plot_thermo",
    "meteogram_plot_weather", "meteogram_plot", "meteogram_request", "meteogram_pdf", "telegram_commands", "telegram_ui",
    "telegram_product_wizard", "telegram_aero", "telegram_windgram", "telegram_cloudgram", "telegram_map", "telegram_route",
    "telegram_route_common", "telegram_meteogram", "telegram_meteogram_common", "telegram_schedules", "telegram_schedule_ux",
    "telegram_bot_core", "telegram_concise_ux", "telegram_result_copy", "messenger.contracts", "messenger.callback_codec",
    "messenger.state", "messenger.platform_config", "messenger.runtime_resources", "messenger.profile_service", "messenger.aero_service",
    "messenger.windgram_service", "messenger.cloudgram_service", "messenger.map_service", "messenger.meteogram_service", "messenger.route_service",
    "messenger.router", "messenger.personal_router", "messenger.aero_router", "messenger.windgram_router", "messenger.cloudgram_router",
    "messenger.map_router", "messenger.meteogram_router", "messenger.route_router", "messenger.user_recipes", "messenger.max.client",
    "messenger.max.adapter", "messenger.max.gateway", "messenger.vk.client", "messenger.vk.adapter", "messenger.vk.gateway",
    "messenger.webhooks", "telegram_profile_common", "telegram_saved_recipes", "telegram_bot", "messenger_launcher",
    "messenger_runtime", "messenger_config_check", "prepare_messenger_config", "register_messenger_webhooks",
)

def check_modules(modules: tuple[str, ...]) -> list[str]:
    errors=[]
    for module_name in modules:
        try: importlib.import_module(module_name)
        except Exception as exc: errors.append(f"{module_name}: {exc}")
    return errors

def main() -> int:
    errors=check_modules(REQUIRED_MODULES)
    if errors: _print_errors(errors); return 1
    import matplotlib; matplotlib.use("Agg", force=True)
    errors=check_modules(OPTIONAL_RUNTIME_MODULES)
    if errors: _print_errors(errors); return 1
    print("Runtime check OK: GFS/GRIB, DaData, shared runtime resources, independent platform lifecycle, common profile/aero/windgram/cloudgram/map/meteogram/route services, messenger registration setup, schedules and Telegram/MAX/VK gateways import successfully")
    return 0

def _print_errors(errors):
    print("Runtime check failed:", file=sys.stderr)
    for error in errors: print(f"  - {error}", file=sys.stderr)

if __name__ == "__main__": raise SystemExit(main())
