from __future__ import annotations

import unittest

from geocode import GeoPoint
from gfs_core import GfsRun
from telegram_bot import _profile_repeat_message, parse_request


class TelegramProfileTests(unittest.TestCase):
    def test_parse_profile_point_and_lead(self) -> None:
        parsed = parse_request("Москва +48")
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual(parsed.lead_hour, 48)
        self.assertTrue(parsed.lead_from_user)

    def test_profile_repeat_command_is_copy_friendly_html_code(self) -> None:
        point = GeoPoint(55.7558, 37.6173, "Москва", "test")
        text = _profile_repeat_message(point, 24, GfsRun("20260701", "12"))
        self.assertIn("<code>/profile 55.7558 37.6173", text)
        self.assertIn("+24</code>", text)


if __name__ == "__main__":
    unittest.main()
