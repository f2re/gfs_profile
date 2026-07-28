from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from map_animation_overlay import _date_labels, _lead_label, decorate_map_animation_frame


class Run:
    date = "20260703"
    cycle = "00"


class MapAnimationOverlayTest(unittest.TestCase):
    def test_labels_from_frame_metadata(self) -> None:
        frame = {"valid_time": datetime(2026, 7, 4, 6, tzinfo=timezone.utc), "lead_hour": 30, "run": Run()}

        self.assertEqual(_date_labels(frame), ("04.07", "06:00 UTC"))
        self.assertEqual(_lead_label(frame), "+030 ч")

    def test_decorate_map_animation_frame_changes_top_panel_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.png"
            Image.new("RGB", (640, 640), "white").save(path)
            frame = {"valid_time": datetime(2026, 7, 4, 6, tzinfo=timezone.utc), "lead_hour": 30, "run": Run()}

            decorate_map_animation_frame(path, frame, index=2, total=5)

            image = Image.open(path).convert("RGB")
            self.assertNotEqual(image.getpixel((32, 32)), (255, 255, 255))
            self.assertEqual(image.getpixel((320, 500)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
