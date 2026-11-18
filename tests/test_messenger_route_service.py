from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geocode import GeoPoint
from gfs_core import GfsRun
from messenger.profile_service import cleanup_product_result
from messenger.route_service import build_route_product_result, parse_route_input, route_plan


class RouteServiceTests(unittest.TestCase):
    def setUp(self):
        self.origin = GeoPoint(55.75, 37.62, "Москва", "test")
        self.destination = GeoPoint(59.94, 30.31, "Санкт-Петербург", "test")

    def test_parse_route_options(self):
        parsed = parse_route_input("Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro")
        self.assertEqual(parsed.origin_query, "Москва")
        self.assertEqual(parsed.destination_query, "Санкт-Петербург")
        self.assertEqual((parsed.departure_lead, parsed.speed_kmh, parsed.spatial_step_km, parsed.mode), (24, 300, 50, "pro"))

    def test_route_plan_uses_eta_max_lead(self):
        distance, duration, specs, max_lead = route_plan(self.origin, self.destination, {"lead": 24, "speed": 300, "spatial_step": 100, "mode": "simple"})
        self.assertGreater(distance, 500)
        self.assertGreater(duration, 1)
        self.assertGreaterEqual(max_lead, 24)
        self.assertGreater(len(specs), 2)

    def test_builder_selects_run_for_route_max_lead_and_returns_png_csv(self):
        run = GfsRun("20260905", "00")
        selected = []
        fake_data = object()
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "route.png"; csv = Path(tmp) / "route.csv"
            def selector(lead): selected.append(lead); return run
            def write_png(data): png.write_bytes(b"png"); return png
            def write_csv(data): csv.write_text("x", encoding="utf-8"); return csv
            with (
                patch("messenger.route_service.build_route_profile_data", return_value=fake_data),
                patch("messenger.route_service.write_route_profile_png", side_effect=write_png),
                patch("messenger.route_service.write_route_csv", side_effect=write_csv),
                patch("messenger.route_service.route_summary", return_value="ROUTE SUMMARY"),
            ):
                result = build_route_product_result(self.origin, self.destination, 24, 300, "simple", 100, None, run_selector=selector)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0], result.metadata["max_lead"])
            self.assertGreaterEqual(selected[0], 24)
            self.assertEqual([a.kind for a in result.attachments], ["image", "file"])
            self.assertNotIn("run", result.repeat_command.split("speed=")[0])  # run is output only, not params signature
            self.assertIn("run=20260905/00", result.repeat_command)
            cleanup_product_result(result)
            self.assertFalse(png.exists()); self.assertFalse(csv.exists())


if __name__ == "__main__": unittest.main()
