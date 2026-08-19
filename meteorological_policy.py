from __future__ import annotations

"""Install the remaining composed meteorological policies.

The composite GFS map no longer needs runtime monkeypatching: ``composite_map``
contains only download/render infrastructure and delegates construction to the
single audited builder in ``composite_map_meteorology``. This installer now
only keeps the route-profile policy wiring that still depends on composition.
"""

import sys
from typing import Any


def install(namespace: dict[str, Any] | None = None) -> None:
    import route_profile_contract
    import route_profile_vertical_policy

    route_profile_vertical_policy.install()

    telegram_route = sys.modules.get("telegram_route")
    if telegram_route is not None:
        telegram_route.build_route_profile_data = route_profile_contract.build_route_profile_data

    if namespace is not None:
        namespace["meteorological_policy_installed"] = True
