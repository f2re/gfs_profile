from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from geocode import GeoPoint
from gfs_core import GfsRun
from messenger.map_service import (
    auto_map_step,
    build_map_product_result,
    normalize_map_params,
    parse_map_input,
)
from messenger.profile_service import cleanup_product_result


class MapServiceTests(unittest.TestCase):
    def test_default_is_48_hour_animation(self) -> None:
        parsed = parse_map_input("Москва")
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual((parsed.lead_from, parsed.lead_to, parsed.step, parsed.mode), (0, 48, 3, "gif"))
        self.assertEqual(parsed.basemap, "places")
        self.assertEqual(parsed.radius_km, 100)

    def test_explicit_lead_is_single_map(self) -> None:
        parsed = parse_map_input("Москва +24")
        self.assertEqual((parsed.lead_from, parsed.lead_to, parsed.mode), (24, 24, "single"))

    def test_long_animation_step_is_adjusted_to_frame_limit(self) -> None:
        self.assertEqual(auto_map_step(0, 96, 3, "gif"), 6)
        params = normalize_map_params({"from": 0, "to": 96, "step": 3, "mode": "gif"})
        self.assertEqual(params["step"], 6)

    def test_builder_selects_run_for_max_lead_and_returns_animation(self) -> None:
        point = GeoPoint(55.75, 37.62, "Москва", "test")
        run = GfsRun("20260905", "00")
        selected = []
        frame = {
            "run": run,
            "lead_hour": 0,
            "point": point,
            "radius_km": 100,
            "missing": set(),
        }
        with tempfile.TemporaryDirectory() as tmp:
            mp4 = Path(tmp) / "map.mp4"
            def selector(lead):
                selected.append(lead); return run
            def frames(*args, **kwargs):
                return [dict(frame, lead_hour=lead) for lead in (0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 48)]
            def video(*args, **kwargs):
                mp4.write_bytes(b"mp4"); return mp4
            with (
                patch("messenger.map_service.build_composite_map_frames", side_effect=frames),
                patch("messenger.map_service.write_composite_map_mp4", side_effect=video),
            ):
                result = build_map_product_result(point, 0, 48, 3, "gif", 100, "places", None, run_selector=selector)
            self.assertEqual(selected, [48])
            self.assertEqual(result.product, "map")
            self.assertEqual(result.metadata["frame_count"], 17)
            self.assertEqual(result.attachments[0].kind, "animation")
            self.assertEqual(result.attachments[0].mime_type, "video/mp4")
            self.assertIn("модельная карта", result.summary)
            self.assertIn("run=20260905/00", result.repeat_command)
            cleanup_product_result(result)
            self.assertFalse(mp4.exists())


if __name__ == "__main__":
    unittest.main()
