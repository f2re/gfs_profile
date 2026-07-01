from __future__ import annotations

import unittest

from telegram_cloudgram import ParsedCloudgramRequest, parse_cloudgram_request


class TelegramCloudgramTests(unittest.TestCase):
    def test_parse_default_cloudgram(self) -> None:
        parsed = parse_cloudgram_request("Краснодар")
        self.assertEqual(parsed.location_query, "Краснодар")
        self.assertEqual(parsed.lead_from, 0)
        self.assertEqual(parsed.lead_to, 72)
        self.assertEqual(parsed.step, 3)
        self.assertEqual(parsed.mode, "pro")

    def test_parse_cloudgram_range_step_and_mode(self) -> None:
        parsed = parse_cloudgram_request("45.0 39.0 from=0 to=120 step=6 mode=simple")
        self.assertEqual(parsed.location_query, "45.0 39.0")
        self.assertEqual(parsed.lead_to, 120)
        self.assertEqual(parsed.step, 6)
        self.assertEqual(parsed.mode, "simple")

    def test_parsed_request_keeps_backward_compatible_default_mode(self) -> None:
        parsed = ParsedCloudgramRequest("45.0 39.0", None, 0, 72, 3)
        self.assertEqual(parsed.mode, "pro")


if __name__ == "__main__":
    unittest.main()
