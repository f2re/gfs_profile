from __future__ import annotations

import unittest

import telegram_bot


class TelegramUxIntegrationTests(unittest.TestCase):
    def test_embedded_core_uses_concise_copy(self) -> None:
        text = telegram_bot.point_prompt_text({"product": "map"})
        self.assertEqual(text, "🗺️ Карта\n\nУкажите город, координаты или отправьте геолокацию.")
        self.assertTrue(getattr(telegram_bot, "_CONCISE_UX_INSTALLED", False))

    def test_embedded_core_uses_temporary_location_keyboard(self) -> None:
        keyboard = telegram_bot._location_keyboard_for_user(0)
        self.assertTrue(keyboard.one_time_keyboard)
        self.assertFalse(keyboard.is_persistent)
        self.assertTrue(any(button.request_location for row in keyboard.keyboard for button in row))

    def test_profile_repeat_message_is_one_command(self) -> None:
        point = telegram_bot.GeoPoint(55.75, 37.62, "Москва", "test")
        run = telegram_bot.GfsRun("20260712", "12")
        message = telegram_bot._profile_repeat_message(point, 24, run)
        self.assertEqual(message.count("<code>"), 1)
        self.assertNotIn("скопируйте", message.lower())


if __name__ == "__main__":
    unittest.main()
