from __future__ import annotations

import unittest
from datetime import timedelta

from cloudgram_product import CloudgramCell, CloudgramData
from geocode import GeoPoint
from gfs_core import GfsRun
from telegram_cloudgram import (
    ParsedCloudgramRequest,
    format_cloudgram_caption,
    format_cloudgram_file_caption,
    format_repeat_cloudgram_message,
    parse_cloudgram_request,
)


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

    def _data(self) -> CloudgramData:
        run = GfsRun("20260701", "12")
        cell = CloudgramCell(
            lead_hour=0,
            valid_time_utc=run.run_datetime_utc,
            high_cloud_pct=10,
            mid_cloud_pct=20,
            low_cloud_pct=30,
            total_cloud_pct=40,
            ceiling_m=1200,
            precip_mm=0,
            precip_rate_mmh=0,
            conv_precip_mm=0,
            precip_type="—",
            cape_jkg=0,
            cin_jkg=0,
            cb_score=0,
            visibility_km=10,
            phenomena="—",
            hazard_score=4,
            hazard_text="гроза",
        )
        second = CloudgramCell(**{**cell.__dict__, "lead_hour": 3, "valid_time_utc": run.run_datetime_utc + timedelta(hours=3)})
        return CloudgramData(run, 55.75, 37.5, 55.75, 37.5, [0, 3], [cell, second])

    def test_caption_is_informative(self) -> None:
        caption = format_cloudgram_caption(self._data(), "simple")
        self.assertIn("Cloudgram GFS 0.25 · SIMPLE", caption)
        self.assertIn("UTC", caption)
        self.assertIn("Узел GFS", caption)
        self.assertIn("4/4 — гроза / очень опасно", caption)

    def test_file_caption_is_compact(self) -> None:
        caption = format_cloudgram_file_caption(self._data(), "simple")
        self.assertIn("PNG · SIMPLE", caption)
        self.assertIn("+0…+3", caption)

    def test_repeat_command_is_copy_friendly_html_code(self) -> None:
        point = GeoPoint(55.7558, 37.6173, "Москва", "test")
        parsed = ParsedCloudgramRequest("Москва", None, 0, 72, 3, "simple")
        text = format_repeat_cloudgram_message(point, parsed, GfsRun("20260701", "12"))
        self.assertIn("<code>/cloudgram 55.7558 37.6173", text)
        self.assertIn("mode=simple</code>", text)


if __name__ == "__main__":
    unittest.main()
