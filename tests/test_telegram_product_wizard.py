from __future__ import annotations

import unittest

from telegram_product_wizard import copy_command, params_keyboard, set_point, start_aero_wizard_state, start_cloudgram_wizard_state, start_map_wizard_state, start_windgram_wizard_state


def _button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


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

    def test_windgram_keyboard_does_not_show_fixed_top_selector(self) -> None:
        state = set_point(start_windgram_wizard_state(), {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        texts = _button_texts(params_keyboard(state))
        self.assertNotIn("✓ top 500 гПа", texts)

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

    def test_map_copy_command_defaults_to_static_lead(self) -> None:
        state = start_map_wizard_state(24)
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        self.assertEqual(copy_command(state), "/map 45.0000 39.0000 +24")

    def test_map_keyboard_has_plus_96_and_two_basemap_modes(self) -> None:
        state = set_point(start_map_wizard_state(24), {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        texts = _button_texts(params_keyboard(state))
        self.assertIn("+96ч", texts)
        self.assertNotIn("+12ч", texts)
        self.assertIn("✓ Полная", texts)
        self.assertIn("Базовая", texts)
        self.assertNotIn("Вода", texts)
        self.assertNotIn("Города", texts)
        self.assertNotIn("Дороги", texts)

    def test_map_animation_keyboard_reaches_plus_96_with_six_hour_step(self) -> None:
        state = set_point(start_map_wizard_state(24), {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["mode"] = "gif"
        state["to"] = 96
        state["time_step"] = 6
        texts = _button_texts(params_keyboard(state))
        self.assertIn("✓ до +96", texts)
        self.assertIn("✓ шаг 6ч", texts)
        self.assertNotIn("шаг 1ч", texts)
        self.assertEqual(copy_command(state), "/map 45.0000 39.0000 from=0 to=96 step=6 mode=gif")

    def test_map_animation_keyboard_allows_one_hour_step_for_short_range(self) -> None:
        state = set_point(start_map_wizard_state(24), {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["mode"] = "gif"
        state["to"] = 12
        texts = _button_texts(params_keyboard(state))
        self.assertIn("шаг 1ч", texts)
        state["time_step"] = 1
        texts = _button_texts(params_keyboard(state))
        self.assertIn("✓ шаг 1ч", texts)
        self.assertIn("✓ до +12", texts)
        self.assertNotIn("до +24", texts)
        self.assertEqual(copy_command(state), "/map 45.0000 39.0000 from=0 to=12 step=1 mode=gif")

    def test_map_copy_command_can_use_png_series(self) -> None:
        state = start_map_wizard_state(24)
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["mode"] = "series"
        state["to"] = 48
        state["time_step"] = 6
        self.assertEqual(copy_command(state), "/map 45.0000 39.0000 from=0 to=48 step=6 mode=series")

    def test_map_copy_command_can_use_animation(self) -> None:
        state = start_map_wizard_state(24)
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["mode"] = "gif"
        state["to"] = 48
        state["time_step"] = 6
        self.assertEqual(copy_command(state), "/map 45.0000 39.0000 from=0 to=48 step=6 mode=gif")

    def test_map_copy_command_can_set_basic_basemap(self) -> None:
        state = start_map_wizard_state(24)
        state = set_point(state, {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"})
        state["basemap"] = "basic"
        self.assertEqual(copy_command(state), "/map 45.0000 39.0000 +24 basemap=basic")


if __name__ == "__main__":
    unittest.main()
