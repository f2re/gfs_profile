from __future__ import annotations

import unittest

from telegram_product_wizard import copy_command, set_point, start_aero_wizard_state, start_cloudgram_wizard_state, start_windgram_wizard_state


class ProductWizardTests(unittest.TestCase):
    def test_aero_copy_command_uses_coordinates_and_params(self) -> None:
        state = start_aero_wizard_state(24, "skewt")
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        self.assertEqual(copy_command(state), "/aero 45.0000 39.0000 +24 type=skewt")

    def test_windgram_copy_command_uses_coordinates_and_params(self) -> None:
        state = start_windgram_wizard_state()
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["to"] = 240
        state["time_step"] = 6
        state["param"] = "temp"
        self.assertEqual(copy_command(state), "/windgram 45.0000 39.0000 from=0 to=240 step=6 top=500 param=temp")

    def test_cloudgram_copy_command_uses_coordinates_and_params(self) -> None:
        state = start_cloudgram_wizard_state()
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["to"] = 72
        state["time_step"] = 3
        self.assertEqual(copy_command(state), "/cloudgram 45.0000 39.0000 from=0 to=72 step=3 mode=pro")

    def test_cloudgram_copy_command_can_use_simple_mode(self) -> None:
        state = start_cloudgram_wizard_state()
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["mode"] = "simple"
        self.assertEqual(copy_command(state), "/cloudgram 45.0000 39.0000 from=0 to=72 step=3 mode=simple")


if __name__ == "__main__":
    unittest.main()
