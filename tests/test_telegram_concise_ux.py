from __future__ import annotations

import unittest
from types import SimpleNamespace

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup

import telegram_concise_ux as ux
from telegram_ui import location_keyboard


class TelegramConciseUxTests(unittest.TestCase):
    def test_start_text_is_short_and_explains_products(self) -> None:
        text = ux.home_text()
        for command in ("/profile", "/route", "/aero", "/windgram", "/cloudgram", "/map"):
            self.assertIn(command, text)
        self.assertLess(len(text), 500)
        for redundant in ("не радар", "не наблюдение", "не радиозонд"):
            self.assertNotIn(redundant, text.lower())

    def test_home_navigation_is_inline(self) -> None:
        keyboard = ux.home_keyboard()
        self.assertIsInstance(keyboard, InlineKeyboardMarkup)
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("home:profile", callbacks)
        self.assertIn("home:route", callbacks)
        self.assertIn("home:map", callbacks)
        self.assertIn("home:help", callbacks)

    def test_location_keyboard_is_temporary_and_stage_specific(self) -> None:
        point = SimpleNamespace(label="Москва", lat=55.75, lon=37.62)
        keyboard = ux.point_keyboard([point])
        self.assertIsInstance(keyboard, ReplyKeyboardMarkup)
        self.assertTrue(keyboard.one_time_keyboard)
        self.assertFalse(keyboard.is_persistent)
        buttons = [button for row in keyboard.keyboard for button in row]
        self.assertTrue(any(button.request_location for button in buttons))
        self.assertTrue(any(button.text == ux.CANCEL_TEXT for button in buttons))

    def test_legacy_location_builder_follows_same_policy(self) -> None:
        keyboard = location_keyboard([])
        self.assertTrue(keyboard.one_time_keyboard)
        self.assertFalse(keyboard.is_persistent)
        self.assertEqual(keyboard.keyboard[-1][0].text, ux.CANCEL_TEXT)

    def test_parameter_summary_has_no_explanatory_paragraphs(self) -> None:
        state = {
            "product": "cloudgram",
            "point": {"label": "Москва", "lat": 55.75, "lon": 37.62},
            "mode": "simple",
            "from": 0,
            "to": 72,
            "time_step": 3,
        }
        text = ux.params_text(state)
        self.assertIn("Москва", text)
        self.assertIn("упрощённый", text)
        self.assertLessEqual(len(text.splitlines()), 4)
        self.assertNotIn("модель", text.lower())

    def test_map_result_status_does_not_repeat_radar_disclaimer(self) -> None:
        ux._patch_product_messages()
        import telegram_map

        data = {
            "run": SimpleNamespace(date="20260712", cycle="12"),
            "point": SimpleNamespace(label="Москва"),
            "lead_hour": 24,
        }
        text = telegram_map.format_map_status(data)
        self.assertIn("Москва", text)
        self.assertNotIn("не радар", text.lower())
        self.assertNotIn("не наблю", text.lower())


if __name__ == "__main__":
    unittest.main()
