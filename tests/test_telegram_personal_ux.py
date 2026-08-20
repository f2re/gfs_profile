from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geocode import GeoPoint
import telegram_user_state
from telegram_personal_ux import (
    _map_command_options,
    _params_from_request,
    _state_params,
    _switch_map_mode,
    home_keyboard,
    home_text,
)
from telegram_user_state import record_product_success, remember_location


class TelegramPersonalUxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prefs.sqlite3"
        self.path_patch = patch.object(telegram_user_state, "DEFAULT_DB_PATH", self.db)
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.tmp.cleanup()

    def point(self) -> GeoPoint:
        return GeoPoint(45.0355, 38.9753, "Краснодар", "test")

    def test_home_shows_active_point_and_quick_action(self) -> None:
        remember_location(1001, self.point())
        record_product_success(
            1001,
            "map",
            {"mode": "gif", "from": 0, "to": 48, "time_step": 3},
            self.point(),
        )
        text = home_text(1001)
        self.assertIn("Краснодар", text)
        self.assertIn("+0…+48", text)
        callbacks = [
            button.callback_data
            for row in home_keyboard(1001).inline_keyboard
            for button in row
        ]
        self.assertIn("quick:map", callbacks)
        self.assertIn("home:settings", callbacks)

    def test_switching_map_mode_restores_each_variant(self) -> None:
        state = {
            "product": "map",
            "mode": "gif",
            "from": 0,
            "to": 48,
            "time_step": 3,
            "basemap": "places",
            "radius": 100,
            "_map_variants": {
                "single": {"lead": 72, "basemap": "basic", "radius": 50},
            },
        }
        single = _switch_map_mode(state, "single")
        self.assertEqual(single["lead"], 72)
        self.assertEqual(single["basemap"], "basic")
        animation = _switch_map_mode(single, "gif")
        self.assertEqual((animation["from"], animation["to"], animation["time_step"]), (0, 48, 3))

    def test_map_state_never_persists_transient_keys(self) -> None:
        params = _state_params(
            {
                "product": "map",
                "step": "params",
                "point": {"lat": 1, "lon": 2},
                "mode": "gif",
                "from": 0,
                "to": 48,
                "time_step": 3,
                "candidates": [1],
                "run": "20260820/00",
            }
        )
        self.assertNotIn("run", params)
        self.assertNotIn("candidates", params)

    def test_map_command_without_time_uses_animation_default(self) -> None:
        options = _map_command_options({}, "Краснодар")
        self.assertIn("from=0", options)
        self.assertIn("to=48", options)
        self.assertIn("step=3", options)
        self.assertIn("mode=gif", options)

    def test_explicit_requests_are_parsed_for_success_memory(self) -> None:
        params = _params_from_request(
            "cloudgram",
            "/cloudgram 45.0 39.0 from=0 to=120 step=6 mode=simple",
            0,
            120,
        )
        self.assertEqual(params, {"from": 0, "to": 120, "time_step": 6, "mode": "simple"})


if __name__ == "__main__":
    unittest.main()
