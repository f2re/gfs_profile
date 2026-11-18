"""Small shared cache for verified forecast times in messenger menus."""
from __future__ import annotations

import asyncio
from time import monotonic

from gfs_core import GfsRun

MENU_RUN_TTL_SECONDS = 180
MENU_RUN_TIMEOUT_SECONDS = 30
UNVERIFIED_TIME_TEXT = "Дату цикла GFS пока не удалось проверить. Повторите выбор срока."


def menu_run(state: dict):
    value = state.get("_forecast_run")
    if isinstance(value, dict):
        return GfsRun(str(value["date"]), str(value["cycle"]))
    return None


async def ensure_menu_run(state: dict, lead: int, selector, *, explicit_run=None):
    """Resolve exactly the requested lead; never infer a published run from time.

    The short per-menu cache avoids new requests during pagination and changes
    to non-time parameters. GFS's own availability cache is shared underneath.
    """
    if explicit_run is not None:
        state["_forecast_lead"] = int(lead)
        return explicit_run
    cached = menu_run(state)
    if (cached is not None and state.get("_forecast_lead") == int(lead)
            and monotonic() - float(state.get("_forecast_checked", 0)) < MENU_RUN_TTL_SECONDS):
        return cached
    token = int(state.get("_forecast_request", 0)) + 1
    state["_forecast_request"] = token
    state["_forecast_lead"] = int(lead)
    state.pop("_forecast_run", None)
    try:
        run = await asyncio.wait_for(asyncio.to_thread(selector, int(lead)), timeout=MENU_RUN_TIMEOUT_SECONDS)
        # Validate the returned date before claiming it in the menu.
        run.run_datetime_utc
    except Exception:
        if state.get("_forecast_request") == token:
            state["_forecast_error"] = UNVERIFIED_TIME_TEXT
        return None
    if state.get("_forecast_request") != token:
        return None
    state.update(_forecast_run={"date": run.date, "cycle": run.cycle},
                 _forecast_lead=int(lead), _forecast_checked=monotonic())
    state.pop("_forecast_error", None)
    return run
