from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

import numpy as np

from composite_map import (
    _area_subset_url,
    _xy_km,
    area_box_from_radius,
    weather_code_icon,
    write_composite_map_png,
)
from composite_map_render import _draw_phenomena_cells
from geocode import GeoPoint
from gfs_core import GfsRun
from weather_diagnostics import DASH


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

    def test_composite_map_has_no_runtime_overpass_or_legacy_grib_reader(self) -> None:
        source = "\n".join(Path(name).read_text(encoding="utf-8") for name in ("composite_map.py", "composite_map_io.py", "composite_map_render.py"))
        forbidden = (
            "OVERPASS_ENDPOINTS",
            "_overpass_query",
            "overpass-api.de",
            "overpass.kumi.systems",
            "openstreetmap.ru",
            "requests.post",
            "cfgrib.open_datasets",
            "def _field(",
            "def _storm_grid(",
            "def _bool_field(",
        )
        for token in forbidden:
            self.assertNotIn(token, source)

    def test_weather_codes_are_rendered_as_unambiguous_icons(self) -> None:
        expected = {
            "RA": "☔",
            "SN": "❄",
            "FZRA": "❄☔",
            "IP": "◆",
            "RASN": "☔❄",
            "UP": "◇",
            "FG": "≋",
            "TS": "⚡",
            "TSRA": "⚡",
            "TSSN": "⚡",
        }
        for code, icon in expected.items():
            self.assertEqual(weather_code_icon(code), icon)
            self.assertNotEqual(icon, code)

    def test_surface_phenomenon_symbols_are_not_stride_sampled_or_truncated(self) -> None:
        source = inspect.getsource(_draw_phenomena_cells)
        self.assertIn("np.argwhere(mask)", source)
        self.assertNotIn("used >=", source)
        self.assertNotIn("range(0, rows, step)", source)

    def test_renderer_smoke_with_synthetic_grid(self) -> None:
        run = GfsRun("20260702", "00")
        point = GeoPoint(45.0, 39.0, "Краснодар", "test")
        axis = np.linspace(-100.0, 100.0, 17)
        x, y = np.meshgrid(axis, axis)
        mask = np.sqrt(x * x + y * y) <= 100.0
        lat = 45.0 + y / 110.574
        lon = 39.0 + x / (111.320 * np.cos(np.radians(45.0)))
        precip = np.maximum(0.0, 8.0 - np.sqrt(x * x + y * y) / 20.0)
        precip_rate = np.maximum(0.0, 2.0 - np.sqrt(x * x + y * y) / 70.0)
        phenomena = np.full(x.shape, DASH, dtype=object)
        phenomena[(precip_rate >= 0.1) & mask] = "RA"
        phenomena[(x > 20) & (y > 20) & (precip_rate >= 0.2) & mask] = "TSRA"
        phenomena[(x < -60) & mask] = "FG"
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
            "precip": precip,
            "precip_interval_hours": 1.0,
            "precip_source": "APCP accumulation",
            "precip_rate_mmh": precip_rate,
            "phenomenon_rate_threshold_mmh": 0.1,
            "phenomenon_code": phenomena,
            "cloud": np.clip(80.0 - y / 3.0, 0.0, 100.0),
            "storm": np.where((x > 0) & (y > 0), 3.0, 0.0),
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
            basemap_overlay = {
                "water_polygons": [[(-20.0, -15.0), (-5.0, -15.0), (-5.0, 0.0), (-20.0, -15.0)]],
                "river_lines": [[(-40.0, -20.0), (0.0, 0.0), (35.0, 20.0)]],
                "road_lines": [[(-30.0, 30.0), (35.0, 35.0)]],
                "admin_lines": [[(-45.0, -45.0), (45.0, -45.0)]],
                "coastline_lines": [[(-50.0, 10.0), (-20.0, 20.0), (10.0, 12.0)]],
                "city_points": [("Тестовый город", 12.0, 15.0)],
                "stats": {
                    "status": "ok",
                    "source": "Natural Earth",
                    "resolution": "10m",
                    "city_count": 1,
                    "water_count": 1,
                    "river_count": 1,
                    "road_count": 1,
                    "admin_count": 1,
                    "coastline_count": 1,
                    "warnings": [],
                },
            }
            out = write_composite_map_png(data, path, pixel_size=480, basemap_overlay=basemap_overlay)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 10_000)
            self.assertIn("Подложка: Natural Earth 10m", data.get("overlay_footer", ""))
            self.assertEqual(data["overlay_summary"]["city_count"], 1)


if __name__ == "__main__":
    unittest.main()
