from __future__ import annotations

import unittest

from geocode import GeoPoint
from telegram_ui import lead_keyboard, lead_page_count, lead_page_text, location_keyboard


class TelegramUiTests(unittest.TestCase):
    def test_common_lead_keyboard_has_full_range_entry(self) -> None:
        markup = lead_keyboard(0)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("+24 ч сутки", labels)
        self.assertIn("Максимум +384 ч", labels)
        self.assertIn("Все сроки →", labels)

    def test_lead_pagination_has_multiple_pages(self) -> None:
        self.assertGreater(lead_page_count(), 1)
        markup = lead_keyboard(1)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("1/", " ".join(labels))
        self.assertIn("Популярные", labels)

    def test_lead_page_text_is_compact(self) -> None:
        self.assertEqual(lead_page_text(0), "Выберите срок.")
        self.assertIn("страница", lead_page_text(1))

    def test_location_keyboard_is_temporary_and_stage_specific(self) -> None:
        markup = location_keyboard(
            [
                GeoPoint(55.7558, 37.6173, "Москва", "test"),
                GeoPoint(44.0393, 43.0708, "Пятигорск", "test"),
                GeoPoint(45.0355, 38.9753, "Краснодар", "test"),
            ]
        )
        rows = [[button.text for button in row] for row in markup.keyboard]
        self.assertEqual(rows[0], ["📍 Моя геолокация"])
        self.assertEqual(rows[1], ["🕘 Москва", "🕘 Пятигорск"])
        self.assertEqual(rows[2], ["🕘 Краснодар"])
        self.assertEqual(rows[-1], ["✖ Отмена"])
        self.assertTrue(markup.one_time_keyboard)
        self.assertFalse(markup.is_persistent)
        self.assertTrue(markup.selective)
        self.assertNotIn("❓ Помощь", [text for row in rows for text in row])


if __name__ == "__main__":
    unittest.main()
