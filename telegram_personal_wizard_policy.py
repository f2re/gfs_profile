from __future__ import annotations

"""Compatibility policy for map period presets in the personalised wizard."""


def install() -> None:
    import telegram_product_wizard as wizard

    if getattr(wizard, "_PERSONAL_MAP_PERIOD_POLICY_INSTALLED", False):
        return

    def map_to_options(state: dict[str, object]) -> tuple[int, ...]:
        lead_from = int(state.get("from", 0))
        lead_to = int(state.get("to", 48))
        step = max(1, int(state.get("time_step", 3)))

        options = {
            value
            for value in wizard.MAP_TO_HOURS
            if value >= lead_from
            and wizard._map_frame_count(lead_from, value, step)
            <= wizard.MAP_MENU_MAX_FRAMES
        }
        # For the normal 3/6-hour workflow expose the useful 48/72/96-hour
        # presets. Selecting one is normalised to a compatible step before
        # rendering. At a one-hour step keep the old safe behaviour: only
        # periods that already fit the Telegram frame limit are displayed.
        if step >= 3:
            options.update(
                value
                for value in (48, 72, 96)
                if value >= lead_from
            )
        if lead_to >= lead_from:
            options.add(lead_to)
        return tuple(sorted(options)) or (max(lead_from, 12),)

    wizard._map_to_options = map_to_options
    wizard._PERSONAL_MAP_PERIOD_POLICY_INSTALLED = True
