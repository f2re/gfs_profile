from __future__ import annotations

import unittest

from geocode import GeoPoint
from gfs_core import GfsRun
from telegram_aero import ParsedAeroRequest, format_aero_file_caption, format_repeat_aero_message, parse_aero_request


class TelegramAeroTests(unittest.TestCase):
    def test_parse_aero_has_one_diagram_type(self) -> None:
        parsed = parse_aero_request("Москва +24", default_lead=12)
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual(parsed.lead_hour, 24)
        self.assertEqual(parsed.diagram_type, "skewt")

    def test_legacy_type_parameter_does_not_change_product(self) -> None:
        parsed = parse_aero_request("Москва +24 type=stuve", default_lead=12)
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual(parsed.diagram_type, "skewt")

    def test_file_caption_is_compact(self) -> None:
        caption = format_aero_file_caption(GfsRun("20260701", "12"), 24)
        self.assertIn("аэрологическая диаграмма", caption)
        self.assertIn("+24", caption)
        self.assertNotIn("Stüve", caption)
        self.assertNotIn("Emagram", caption)

    def test_repeat_command_has_no_type_selector(self) -> None:
        point = GeoPoint(55.7558, 37.6173, "Москва", "test")
        parsed = ParsedAeroRequest("Москва", 24, None)
        text = format_repeat_aero_message(point, parsed, GfsRun("20260701", "12"))
        self.assertIn("<code>/aero 55.7558 37.6173", text)
        self.assertNotIn("type=", text)


if __name__ == "__main__":
    unittest.main()
