from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from messenger.aero_service import build_aero_product_result, parse_aero_input
from messenger.profile_service import cleanup_product_result


class AeroServiceTests(unittest.TestCase):
    def test_parser_ignores_legacy_type_and_detects_explicit_lead(self) -> None:
        parsed = parse_aero_input("Москва type=stuve +48", 24)
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual(parsed.lead_hour, 48)
        self.assertTrue(parsed.lead_from_user)
        self.assertEqual(parsed.diagram_type, "skewt")

    def test_parser_preserves_explicit_run_without_forcing_lead(self) -> None:
        parsed = parse_aero_input("Москва run=20260904/06", 24)
        self.assertEqual((parsed.run.date, parsed.run.cycle), ("20260904", "06"))
        self.assertEqual(parsed.lead_hour, 24)
        self.assertFalse(parsed.lead_from_user)

    def test_common_result_contains_model_contract_and_png(self) -> None:
        run = SimpleNamespace(date="20260904", cycle="06")
        point = SimpleNamespace(lat=45.0355, lon=38.9753, label="Краснодар", source="test")
        profile = SimpleNamespace(
            run=run,
            lead_hour=24,
            valid_time_utc=datetime(2026, 9, 5, 6, tzinfo=timezone.utc),
            requested_lat=45.0355,
            requested_lon=38.9753,
            grid_lat=45.0,
            grid_lon=39.0,
            dataframe=[1, 2, 3],
        )
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "aero.png"
            png.write_bytes(b"png")
            with patch("gfs_core.latest_available_run_for_lead", return_value=run) as latest:
                with patch("aero_product.build_aero_product", return_value=(profile, png)) as builder:
                    result = build_aero_product_result(
                        point,
                        24,
                        progress_callback=events.append,
                    )
            self.assertEqual(result.product, "aero")
            self.assertIn("GFS 0.25", result.summary)
            self.assertIn("Skew-T", result.summary)
            self.assertIn("модель, не радиозонд", result.summary)
            self.assertEqual(result.metadata["run_date"], "20260904")
            self.assertEqual(result.metadata["grid_lon"], 39.0)
            self.assertTrue(result.metadata["icing_cat_are_model_proxies"])
            self.assertEqual(len(result.attachments), 1)
            self.assertEqual(result.attachments[0].kind, "image")
            self.assertIn("/aero", result.repeat_command)
            self.assertIn("run=20260904/06", result.repeat_command)
            latest.assert_called_once_with(24)
            self.assertEqual(builder.call_args.args[:4], (run, 24, 45.0355, 38.9753))
            self.assertEqual(events[0].stage, "check")
            self.assertEqual(events[1].stage, "run")
            cleanup_product_result(result)
            self.assertFalse(png.exists())

    def test_explicit_run_is_used_without_latest_lookup(self) -> None:
        run = SimpleNamespace(date="20260904", cycle="00")
        point = SimpleNamespace(lat=55.75, lon=37.62, label="Москва", source="test")
        profile = SimpleNamespace(
            run=run,
            lead_hour=12,
            valid_time_utc=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
            requested_lat=55.75,
            requested_lon=37.62,
            grid_lat=55.75,
            grid_lon=37.5,
            dataframe=[1],
        )
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "aero.png"
            png.write_bytes(b"png")
            with patch("gfs_core.latest_available_run_for_lead") as latest:
                with patch("aero_product.build_aero_product", return_value=(profile, png)):
                    result = build_aero_product_result(point, 12, run)
            latest.assert_not_called()
            cleanup_product_result(result)


if __name__ == "__main__":
    unittest.main()
