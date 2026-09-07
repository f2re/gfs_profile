from __future__ import annotations

import unittest

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import telegram_weathernext3 as wn3


class TelegramWeatherNext3Tests(unittest.TestCase):
    def test_home_button_is_inserted_once(self):
        base = InlineKeyboardMarkup([[InlineKeyboardButton("GFS", callback_data="home:profile")]])
        first = wn3._insert_wn3_button(base)
        second = wn3._insert_wn3_button(first)
        callbacks = [button.callback_data for row in second.inline_keyboard for button in row]
        self.assertEqual(callbacks.count("home:wn3"), 1)

    def test_single_map_has_explicit_leads(self):
        keyboard = wn3._card_keyboard({"kind": "clouds", "mode": "single", "from": 24, "to": 24})
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("wn3:lead:24", callbacks)
        self.assertNotIn("wn3:to:48", callbacks)

    def test_default_card_is_point_plus_24(self):
        params = wn3.normalize_wn3_params({})
        self.assertEqual(params["kind"], "point")
        self.assertEqual(params["hours"], 24)
        self.assertEqual((params["from"], params["to"], params["step"]), (1, 48, 3))


if __name__ == "__main__":
    unittest.main()
