from __future__ import annotations

import unittest
from datetime import timedelta

from geocode import GeoPoint
from gfs_core import GfsRun
from telegram_windgram import ParsedWindgramRequest, format_repeat_windgram_message, format_windgram_caption, format_windgram_file_caption, parse_windgram_request
from windgram_product import WindgramCell, WindgramData


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

    def _data(self) -> WindgramData:
        run = GfsRun("20260701", "12")
        cells = [
            WindgramCell(
                lead_hour=0,
                valid_time_utc=run.run_datetime_utc,
                level_hpa=1000,
                height_m=100,
                wind_speed_ms=5,
                wind_direction_deg=270,
                u_ms=5,
                v_ms=0,
                temperature_c=10,
                relative_humidity_pct=80,
            ),
            WindgramCell(
                lead_hour=3,
                valid_time_utc=run.run_datetime_utc + timedelta(hours=3),
                level_hpa=1000,
                height_m=100,
                wind_speed_ms=6,
                wind_direction_deg=280,
                u_ms=6,
                v_ms=0,
                temperature_c=11,
                relative_humidity_pct=82,
            ),
        ]
        return WindgramData(run, 55.75, 37.5, 55.75, 37.5, [0, 3], [1000], cells, param="temp")

    def test_windgram_caption_is_informative(self) -> None:
        caption = format_windgram_caption(self._data())
        self.assertIn("Windgram GFS 0.25", caption)
        self.assertIn("UTC", caption)
        self.assertIn("Узел GFS", caption)
        self.assertIn("температура", caption)

    def test_windgram_file_caption_is_compact(self) -> None:
        caption = format_windgram_file_caption(self._data())
        self.assertIn("PNG · WINDGRAM", caption)
        self.assertIn("+0…+3", caption)

    def test_repeat_command_is_copy_friendly_html_code(self) -> None:
        point = GeoPoint(55.7558, 37.6173, "Москва", "test")
        parsed = ParsedWindgramRequest("Москва", None, 0, 120, 6, 500, "temp")
        text = format_repeat_windgram_message(point, parsed, GfsRun("20260701", "12"))
        self.assertIn("<code>/windgram 55.7558 37.6173", text)
        self.assertIn("param=temp</code>", text)


if __name__ == "__main__":
    unittest.main()
