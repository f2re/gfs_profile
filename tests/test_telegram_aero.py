from __future__ import annotations

import unittest

from geocode import GeoPoint
from gfs_core import GfsRun
from telegram_aero import ParsedAeroRequest, format_aero_file_caption, format_repeat_aero_message, parse_aero_request


class TelegramAeroTests(unittest.TestCase):
    def test_parse_aero_type_and_lead(self) -> None:
        parsed = parse_aero_request("Москва +24 type=skewt", default_lead=12)
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual(parsed.lead_hour, 24)
        self.assertEqual(parsed.diagram_type, "skewt")

    def test_file_caption_is_compact(self) -> None:
        caption = format_aero_file_caption(GfsRun("20260701", "12"), 24, "skewt")
        self.assertIn("PNG · Skew-T", caption)
        self.assertIn("+24", caption)
        self.assertIn("UTC", caption)

    def test_repeat_command_is_copy_friendly_html_code(self) -> None:
        point = GeoPoint(55.7558, 37.6173, "Москва", "test")
        parsed = ParsedAeroRequest("Москва", 24, None, "skewt")
        text = format_repeat_aero_message(point, parsed, GfsRun("20260701", "12"))
        self.assertIn("<code>/aero 55.7558 37.6173", text)
        self.assertIn("type=skewt</code>", text)


if __name__ == "__main__":
    unittest.main()
