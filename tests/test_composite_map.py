from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from composite_map import _area_subset_url, _storm_grid, _xy_km, area_box_from_radius, write_composite_map_png
from geocode import GeoPoint
from gfs_core import GfsRun


class CompositeMapTests(unittest.TestCase):
    def test_area_box_from_radius(self) -> None:
        south, north, west, east = area_box_from_radius(45.0, 39.0, 100.0)
        self.assertLess(south, 45.0)
        self.assertGreater(north, 45.0)
        self.assertLess(west, 39.0)
        self.assertGreater(east, 39.0)

    def test_area_box_keeps_negative_longitudes_around_greenwich(self) -> None:
        box = area_box_from_radius(51.5, -0.1, 100.0)
        self.assertLess(box[2], 0.0)
        self.assertGreater(box[3], 0.0)
        url = _area_subset_url("20260702", "00", 24, box)
        self.assertIn("leftlon=-", url)
        self.assertIn("rightlon=", url)

    def test_xy_uses_shortest_longitude_delta_across_antimeridian(self) -> None:
        lat = np.array([[0.0, 0.0]])
        lon = np.array([[179.5, -179.5]])
        x, _, _ = _xy_km(lat, lon, 0.0, 179.8)
        self.assertLess(abs(float(x[0, 1])), 100.0)

    def test_storm_grid_uses_shared_diagnostic_thresholds(self) -> None:
        cape = np.array([[1200.0, 100.0]])
        cin = np.array([[-50.0, -300.0]])
        conv = np.array([[0.5, 0.0]])
        cloud = np.array([[40.0, 10.0]])
        rate = np.array([[4.0, 0.0]])
        storm = _storm_grid(cape, cin, conv, cloud, rate)
        self.assertEqual(float(storm[0, 0]), 3.0)
        self.assertEqual(float(storm[0, 1]), 0.0)

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
            "basemap": "places",
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
