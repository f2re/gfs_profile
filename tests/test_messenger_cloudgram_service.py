from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from geocode import GeoPoint
from gfs_core import GfsRun
from messenger.cloudgram_service import (
    build_cloudgram_product_result,
    normalize_cloudgram_mode,
    parse_cloudgram_input,
)
from messenger.profile_service import cleanup_product_result


class CloudgramServiceTests(unittest.TestCase):
    def test_parser_keeps_existing_defaults_and_aliases(self) -> None:
        parsed = parse_cloudgram_input("Москва mode=кратко to=120 step=6")
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual((parsed.lead_from, parsed.lead_to, parsed.step), (0, 120, 6))
        self.assertEqual(parsed.mode, "simple")
        self.assertEqual(normalize_cloudgram_mode("подробно"), "pro")

    def test_service_selects_run_for_max_lead_and_returns_model_contract(self) -> None:
        point = GeoPoint(55.75, 37.62, "Москва", "test")
        run = GfsRun("20260905", "00")
        selected = []
        progress = []
        valid0 = datetime(2026, 9, 5, 0, tzinfo=timezone.utc)
        cells = [
            SimpleNamespace(lead_hour=0, valid_time_utc=valid0, hazard_score=1),
            SimpleNamespace(lead_hour=72, valid_time_utc=valid0 + timedelta(hours=72), hazard_score=4),
        ]
        data = SimpleNamespace(
            run=run,
            requested_lat=55.75,
            requested_lon=37.62,
            grid_lat=55.75,
            grid_lon=37.5,
            leads=[0, 72],
            cells=cells,
            missing_fields=(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "cloudgram.png"

            def selector(lead):
                selected.append(lead)
                return run

            def build(*args, **kwargs):
                return data

            def plot(*args, **kwargs):
                png.write_bytes(b"png")
                return png

            with (
                patch("messenger.cloudgram_service.build_cloudgram_data", side_effect=build),
                patch("messenger.cloudgram_service.write_cloudgram_png", side_effect=plot),
            ):
                result = build_cloudgram_product_result(
                    point,
                    0,
                    72,
                    3,
                    "pro",
                    None,
                    progress_callback=progress.append,
                    run_selector=selector,
                )

            self.assertEqual(selected, [72])
            self.assertEqual(result.product, "cloudgram")
            self.assertEqual(result.metadata["run_date"], "20260905")
            self.assertEqual(result.metadata["grid_lon"], 37.5)
            self.assertEqual(result.metadata["max_hazard"], 4)
            self.assertIn("модель", result.summary)
            self.assertIn("не радиозонд", result.summary)
            self.assertIn("run=20260905/00", result.repeat_command)
            self.assertTrue(result.attachments[0].path.exists())
            cleanup_product_result(result)
            self.assertFalse(png.exists())


if __name__ == "__main__":
    unittest.main()
