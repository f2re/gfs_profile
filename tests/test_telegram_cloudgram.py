from __future__ import annotations

import unittest

from telegram_cloudgram import parse_cloudgram_request


class TelegramCloudgramTests(unittest.TestCase):
    def test_parse_default_cloudgram(self) -> None:
        parsed = parse_cloudgram_request("Краснодар")
        self.assertEqual(parsed.location_query, "Краснодар")
        self.assertEqual(parsed.lead_from, 0)
        self.assertEqual(parsed.lead_to, 72)
        self.assertEqual(parsed.step, 3)

    def test_parse_cloudgram_range_and_step(self) -> None:
        parsed = parse_cloudgram_request("45.0 39.0 from=0 to=120 step=6")
        self.assertEqual(parsed.location_query, "45.0 39.0")
        self.assertEqual(parsed.lead_to, 120)
        self.assertEqual(parsed.step, 6)


if __name__ == "__main__":
    unittest.main()
