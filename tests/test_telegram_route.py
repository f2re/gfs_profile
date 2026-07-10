from __future__ import annotations

import unittest

from telegram_route import parse_route_request


class TelegramRouteTests(unittest.TestCase):
    def test_parse_route_request(self) -> None:
        parsed = parse_route_request("Москва -> Санкт-Петербург +24 speed=300 mode=pro")
        self.assertEqual(parsed.origin_query, "Москва")
        self.assertEqual(parsed.destination_query, "Санкт-Петербург")
        self.assertEqual(parsed.departure_lead, 24)
        self.assertEqual(parsed.speed_kmh, 300)
        self.assertEqual(parsed.mode, "pro")

    def test_parse_coordinate_route(self) -> None:
        parsed = parse_route_request("55.75 37.62 → 59.94 30.31 +6 speed=450 mode=simple")
        self.assertEqual(parsed.origin_query, "55.75 37.62")
        self.assertEqual(parsed.destination_query, "59.94 30.31")
        self.assertEqual(parsed.departure_lead, 6)
        self.assertEqual(parsed.speed_kmh, 450)

    def test_rejects_missing_separator(self) -> None:
        with self.assertRaises(ValueError):
            parse_route_request("Москва Санкт-Петербург +24")


if __name__ == "__main__":
    unittest.main()
