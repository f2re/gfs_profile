from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from geocode import GeoPoint
from gfs_core import GfsRun
from messenger.windgram_service import build_windgram_product_result, parse_windgram_input
from windgram_product import WindgramCell, WindgramData


class WindgramServiceTests(unittest.TestCase):
    def test_parser_keeps_operational_defaults_and_aliases(self) -> None:
        parsed = parse_windgram_input("Москва param=влажность")
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual((parsed.lead_from, parsed.lead_to, parsed.step, parsed.top_hpa), (0, 120, 6, 500))
        self.assertEqual(parsed.param, "rh")

    def test_parser_preserves_explicit_run_and_range(self) -> None:
        parsed = parse_windgram_input("Краснодар run=20260905/00 from=12 to=240 step=12 top=700 param=temp")
        self.assertEqual(parsed.location_query, "Краснодар")
        self.assertEqual((parsed.run.date, parsed.run.cycle), ("20260905", "00"))
        self.assertEqual((parsed.lead_from, parsed.lead_to, parsed.step, parsed.top_hpa, parsed.param), (12, 240, 12, 700, "temp"))

    def test_builder_selects_run_for_max_lead_and_returns_common_result(self) -> None:
        point = GeoPoint(55.7558, 37.6173, "Москва", "test")
        run = GfsRun("20260905", "00")
        cells = [
            WindgramCell(0, run.run_datetime_utc, 1000, 120, 15, 70, 5, 0, 5, 270),
            WindgramCell(120, run.run_datetime_utc + timedelta(hours=120), 1000, 120, 10, 80, 15, 0, 15, 270),
        ]
        data = WindgramData(run, point.lat, point.lon, 55.75, 37.5, [0, 120], [1000, 500], cells, param="wind")

        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "windgram.png"
            png.write_bytes(b"png")
            progress = []
            with (
                patch("messenger.windgram_service.latest_available_run_for_lead", return_value=run) as selector,
                patch("messenger.windgram_service.build_windgram_data", return_value=data) as builder,
                patch("messenger.windgram_service.write_windgram_png", return_value=png),
            ):
                result = build_windgram_product_result(
                    point,
                    0,
                    120,
                    6,
                    500,
                    "wind",
                    None,
                    progress_callback=progress.append,
                )

            selector.assert_called_once_with(120)
            builder.assert_called_once()
            self.assertEqual(result.product, "windgram")
            self.assertEqual(result.metadata["run_date"], "20260905")
            self.assertEqual(result.metadata["lead_to"], 120)
            self.assertEqual(result.metadata["max_wind_ms"], 15.0)
            self.assertIn("Москва", result.summary)
            self.assertIn("GFS grid", result.summary)
            self.assertIn("модель, не наблюдение", result.summary)
            self.assertIn("run=20260905/00", result.repeat_command)
            self.assertTrue(any(event.stage == "run" for event in progress))
            self.assertEqual(result.attachments[0].path, png)


if __name__ == "__main__":
    unittest.main()
