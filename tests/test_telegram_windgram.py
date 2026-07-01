from __future__ import annotations

import unittest

from telegram_windgram import parse_windgram_request


class TelegramWindgramTests(unittest.TestCase):
    def test_parse_param_temp(self) -> None:
        parsed = parse_windgram_request("Краснодар to=120 step=6 top=500 param=temp")
        self.assertEqual(parsed.location_query, "Краснодар")
        self.assertEqual(parsed.lead_to, 120)
        self.assertEqual(parsed.step, 6)
        self.assertEqual(parsed.top_hpa, 500)
        self.assertEqual(parsed.param, "temp")

    def test_parse_param_rh_alias(self) -> None:
        parsed = parse_windgram_request("45.0 39.0 param=влажность")
        self.assertEqual(parsed.location_query, "45.0 39.0")
        self.assertEqual(parsed.param, "rh")


if __name__ == "__main__":
    unittest.main()
