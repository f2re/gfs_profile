from __future__ import annotations

"""Install audited meteorological implementations into composed Telegram flows."""

import sys
from typing import Any


def install(namespace: dict[str, Any] | None = None) -> None:
    import composite_map
    import composite_map_meteorology
    import map_animation
    import route_profile_contract
    import route_profile_vertical_policy
    import telegram_map

    if getattr(composite_map, "_AUDITED_METEOROLOGY_INSTALLED", False):
        route_profile_vertical_policy.install()
        return

    # Core builders are safe to replace in the source module. The PNG writer is
    # deliberately left as the original implementation: the audited wrapper
    # delegates to it after installing the corrected legend. Replacing it with
    # the delegating wrapper would create recursion.
    for name in ("build_composite_map", "build_composite_map_frames"):
        implementation = getattr(composite_map_meteorology, name)
        setattr(composite_map, name, implementation)
        setattr(telegram_map, name, implementation)

    for name in ("write_composite_map_png", "write_composite_map_gif"):
        setattr(telegram_map, name, getattr(composite_map_meteorology, name))

    map_animation.write_composite_map_png = composite_map_meteorology.write_composite_map_png
    route_profile_vertical_policy.install()

    telegram_route = sys.modules.get("telegram_route")
    if telegram_route is not None:
        telegram_route.build_route_profile_data = route_profile_contract.build_route_profile_data

    if namespace is not None:
        namespace["meteorological_policy_installed"] = True
    composite_map._AUDITED_METEOROLOGY_INSTALLED = True
