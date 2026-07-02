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
    "PIL",
    "metpy",
    "scipy",
    "pint",
)

OPTIONAL_RUNTIME_MODULES = (
    "plot_style",
    "profile_plot_ru",
    "gfs_subset",
    "aero_plot",
    "aero_product",
    "windgram_product",
    "windgram_plot",
    "cloudgram_product",
    "cloudgram_plot",
    "weather_diagnostics",
    "user_location_session",
    "composite_map",
    "telegram_commands",
    "telegram_product_wizard",
    "telegram_aero",
    "telegram_windgram",
    "telegram_cloudgram",
    "telegram_map",
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
    print("Runtime check OK: dependencies, command definitions, map/cloudgram, wizard and bot modules import successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
