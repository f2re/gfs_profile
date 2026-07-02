from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from composite_map import area_box_from_radius, write_composite_map_png
from geocode import GeoPoint
from gfs_core import GfsRun


class CompositeMapTests(unittest.TestCase):
    def test_area_box_from_radius(self) -> None:
        south, north, west, east = area_box_from_radius(45.0, 39.0, 100.0)
        self.assertLess(south, 45.0)
        self.assertGreater(north, 45.0)
        self.assertLess(west, 39.0)
        self.assertGreater(east, 39.0)

    def test_renderer_smoke_with_synthetic_grid(self) -> None:
        run = GfsRun("20260702", "00")
        point = GeoPoint(45.0, 39.0, "Краснодар", "test")
        axis = np.linspace(-100.0, 100.0, 17)
        x, y = np.meshgrid(axis, axis)
        mask = np.sqrt(x * x + y * y) <= 100.0
        lat = 45.0 + y / 110.574
        lon = 39.0 + x / (111.320 * np.cos(np.radians(45.0)))
        data = {
            "run": run,
            "lead_hour": 24,
            "valid_time": run.run_datetime_utc,
            "point": point,
            "radius_km": 100.0,
            "box": (44.0, 46.0, 38.0, 40.0),
            "x": x,
            "y": y,
            "dist": np.sqrt(x * x + y * y),
            "mask": mask,
            "lat": lat,
            "lon": lon,
            "precip": np.maximum(0.0, 8.0 - np.sqrt(x * x + y * y) / 20.0),
            "cloud": np.clip(80.0 - y / 3.0, 0.0, 100.0),
            "storm": np.where((x > 0) & (y > 0), 2.0, 0.0),
            "visibility": np.where(x < -40.0, 3.0, 12.0),
            "u500": np.full_like(x, 12.0),
            "v500": np.full_like(y, -4.0),
            "rain": np.ones_like(mask, dtype=bool),
            "snow": np.zeros_like(mask, dtype=bool),
            "cold": np.zeros_like(mask, dtype=bool),
            "ice": np.zeros_like(mask, dtype=bool),
            "missing": set(),
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.png"
            out = write_composite_map_png(data, path, pixel_size=480, overlay={"elements": []})
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
