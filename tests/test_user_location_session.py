from __future__ import annotations

import unittest

from geocode import GeoPoint
from user_location_session import clear_recent_locations, get_recent_locations, match_recent_location_button, recent_location_button_label, remember_location


class UserLocationSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_recent_locations(1001)

    def tearDown(self) -> None:
        clear_recent_locations(1001)

    def test_remember_locations_most_recent_first_with_limit(self) -> None:
        for index in range(6):
            remember_location(1001, GeoPoint(50.0 + index, 30.0 + index, f"Город {index}", "test"))

        recent = get_recent_locations(1001)
        self.assertEqual([point.label for point in recent], ["Город 5", "Город 4", "Город 3", "Город 2"])

    def test_duplicate_by_close_coordinates_moves_to_front(self) -> None:
        remember_location(1001, GeoPoint(55.7558, 37.6173, "Москва", "test"))
        remember_location(1001, GeoPoint(45.0448, 41.9690, "Ставрополь", "test"))
        remember_location(1001, GeoPoint(55.7560, 37.6180, "Moscow", "test"))

        recent = get_recent_locations(1001)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0].label, "Moscow")

    def test_match_recent_location_button(self) -> None:
        point = GeoPoint(44.0393, 43.0708, "Пятигорск", "test")
        remember_location(1001, point)
        matched = match_recent_location_button(1001, recent_location_button_label(point))
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched.label, "Пятигорск")

    def test_short_recent_button_keeps_full_session_label_without_numbers(self) -> None:
        long_label = "Очень длинный адрес с районом, улицей, домом и уточнением подъезда"
        point = GeoPoint(44.0393, 43.0708, long_label, "test")
        remember_location(1001, point)

        button_text = recent_location_button_label(point, max_chars=18, index=1)
        self.assertTrue(button_text.startswith("🕘 "))
        self.assertFalse(button_text.startswith("🕘 1. "))
        self.assertIn("…", button_text)
        matched = match_recent_location_button(1001, button_text)

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched.label, long_label)
        self.assertNotIn("…", matched.label)

    def test_legacy_numbered_recent_button_still_matches(self) -> None:
        point = GeoPoint(44.0393, 43.0708, "Пятигорск", "test")
        remember_location(1001, point)
        matched = match_recent_location_button(1001, "🕘 1. Пятигорск")
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched.label, "Пятигорск")

    def test_button_label_falls_back_to_coordinates(self) -> None:
        label = recent_location_button_label(GeoPoint(55.7558, 37.6173, "геолокация Telegram", "telegram"))
        self.assertEqual(label, "🕘 55.7558, 37.6173")


if __name__ == "__main__":
    unittest.main()
