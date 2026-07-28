from __future__ import annotations

"""Install audited meteorological implementations into composed Telegram flows."""

from typing import Any


def install(namespace: dict[str, Any] | None = None) -> None:
    import composite_map
    import composite_map_meteorology
    import map_animation
    import route_profile_vertical_policy
    import telegram_map

    if getattr(composite_map, "_AUDITED_METEOROLOGY_INSTALLED", False):
        route_profile_vertical_policy.install()
        return

    for name in (
        "build_composite_map",
        "build_composite_map_frames",
        "write_composite_map_png",
        "write_composite_map_gif",
    ):
        implementation = getattr(composite_map_meteorology, name)
        setattr(composite_map, name, implementation)
        setattr(telegram_map, name, implementation)

    map_animation.write_composite_map_png = composite_map_meteorology.write_composite_map_png
    route_profile_vertical_policy.install()

    if namespace is not None:
        namespace["meteorological_policy_installed"] = True
    composite_map._AUDITED_METEOROLOGY_INSTALLED = True
