from __future__ import annotations

import unittest

from geocode import local_lookup, parse_coordinates
from telegram_bot import parse_request


class BotHelperTests(unittest.TestCase):
    def test_parse_coordinates(self) -> None:
        point = parse_coordinates("59.93 30.31")
        self.assertIsNotNone(point)
        assert point is not None
        self.assertAlmostEqual(point.lat, 59.93)
        self.assertAlmostEqual(point.lon, 30.31)

    def test_local_lookup(self) -> None:
        point = local_lookup("СПб")
        self.assertIsNotNone(point)
        assert point is not None
        self.assertEqual(point.label, "Санкт-Петербург")

    def test_parse_profile_request_city_and_lead(self) -> None:
        parsed = parse_request("Москва +24")
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual(parsed.lead_hour, 24)
        self.assertIsNone(parsed.run)

    def test_parse_profile_request_expert_run(self) -> None:
        parsed = parse_request("Санкт-Петербург run=20260630/06 +48")
        self.assertEqual(parsed.location_query, "Санкт-Петербург")
        self.assertEqual(parsed.lead_hour, 48)
        self.assertIsNotNone(parsed.run)
        assert parsed.run is not None
        self.assertEqual(parsed.run.date, "20260630")
        self.assertEqual(parsed.run.cycle, "06")


if __name__ == "__main__":
    unittest.main()
