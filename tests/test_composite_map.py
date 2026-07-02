from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import xarray as xr

from composite_map import _area_subset_url, _field, _overpass_query, _storm_grid, _xy_km, area_box_from_radius, extract_overlay_shapes, load_overlay, summarize_overlay, weather_code_icon, write_composite_map_png
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

    def test_overpass_geometry_parser_extracts_all_shape_types(self) -> None:
        overlay = {
            "_meta": {"status": "ok", "basemap": "roads", "endpoint": "test", "element_counts": {}},
            "elements": [
                {"type": "node", "id": 1, "lat": 45.0, "lon": 39.0, "tags": {"place": "city", "name": "Центр"}},
                {
                    "type": "way",
                    "id": 2,
                    "tags": {"natural": "water", "name": "Озеро"},
                    "geometry": [
                        {"lat": 45.00, "lon": 39.00},
                        {"lat": 45.00, "lon": 39.02},
                        {"lat": 45.02, "lon": 39.02},
                        {"lat": 45.00, "lon": 39.00},
                    ],
                },
                {
                    "type": "way",
                    "id": 3,
                    "tags": {"waterway": "river", "name": "Река"},
                    "geometry": [{"lat": 44.99, "lon": 38.99}, {"lat": 45.03, "lon": 39.03}],
                },
                {
                    "type": "way",
                    "id": 4,
                    "tags": {"highway": "primary", "name": "Дорога"},
                    "geometry": [{"lat": 45.01, "lon": 38.99}, {"lat": 45.02, "lon": 39.04}],
                },
                {
                    "type": "relation",
                    "id": 5,
                    "tags": {"natural": "water", "name": "Водохранилище"},
                    "members": [
                        {
                            "type": "way",
                            "role": "outer",
                            "geometry": [
                                {"lat": 45.03, "lon": 39.00},
                                {"lat": 45.03, "lon": 39.02},
                                {"lat": 45.05, "lon": 39.02},
                            ],
                        }
                    ],
                },
            ],
        }
        shapes = extract_overlay_shapes(overlay, 45.0, 39.0, 100.0)
        self.assertEqual(len(shapes["city_points"]), 1)
        self.assertEqual(len(shapes["water_polygons"]), 2)
        self.assertEqual(len(shapes["river_lines"]), 1)
        self.assertEqual(len(shapes["road_lines"]), 1)
        self.assertGreaterEqual(shapes["stats"]["objects_in_view"], 5)

    def test_summarize_overlay_counts_in_and_out_of_view(self) -> None:
        overlay = {
            "_meta": {"status": "ok", "basemap": "places", "element_counts": {}},
            "elements": [
                {"type": "node", "id": 1, "lat": 45.0, "lon": 39.0, "tags": {"place": "town", "name": "Рядом"}},
                {"type": "node", "id": 2, "lat": 50.0, "lon": 39.0, "tags": {"place": "town", "name": "Далеко"}},
            ],
        }
        summary = summarize_overlay(overlay, 45.0, 39.0)
        self.assertEqual(summary["raw_elements"], 2)
        self.assertEqual(summary["city_count"], 1)
        self.assertEqual(summary["objects_out_of_view"], 1)

    def test_basemap_queries_match_modes(self) -> None:
        box = (44.0, 46.0, 38.0, 40.0)
        self.assertEqual(load_overlay(box, "basic")["_meta"]["status"], "disabled")
        water_query = _overpass_query(box, "water")
        self.assertIn('"natural"="water"', water_query)
        self.assertIn('"waterway"~"river|canal|stream"', water_query)
        self.assertNotIn('"place"', water_query)
        places_query = _overpass_query(box, "places")
        self.assertIn('"place"~"city|town|village|hamlet"', places_query)
        self.assertNotIn('"highway"', places_query)
        roads_query = _overpass_query(box, "roads")
        self.assertIn('"highway"~"motorway|trunk|primary|secondary"', roads_query)
        self.assertNotIn("tertiary", roads_query)

    def test_at500_field_extraction_accepts_isobaric_in_pa_and_uppercase_names(self) -> None:
        lat = np.array([45.0, 45.25])
        lon = np.array([39.0, 39.25])
        u_values = np.ones((1, 2, 2), dtype=float) * 12.0
        v_values = np.ones((1, 2, 2), dtype=float) * -4.0
        ds = xr.Dataset(
            {
                "UGRD": (("isobaricInPa", "latitude", "longitude"), u_values),
                "VGRD": (("isobaricInPa", "latitude", "longitude"), v_values),
            },
            coords={"isobaricInPa": [50000.0], "latitude": lat, "longitude": lon},
        )
        u_item = _field([ds], ("u", "ugrd", "UGRD"), level_hpa=500)
        v_item = _field([ds], ("v", "vgrd", "VGRD"), level_hpa=500)
        self.assertIsNotNone(u_item)
        self.assertIsNotNone(v_item)
        self.assertEqual(float(u_item[0][0, 0]), 12.0)
        self.assertEqual(float(v_item[0][0, 0]), -4.0)

        wrong_level = xr.Dataset(
            {"UGRD": (("isobaricInhPa", "latitude", "longitude"), u_values)},
            coords={"isobaricInhPa": [700.0], "latitude": lat, "longitude": lon},
        )
        self.assertIsNone(_field([wrong_level], ("u", "ugrd", "UGRD"), level_hpa=500))

    def test_weather_codes_are_rendered_as_icons(self) -> None:
        self.assertEqual(weather_code_icon("RA"), "☔")
        self.assertEqual(weather_code_icon("SN"), "❄")
        self.assertEqual(weather_code_icon("FZRA"), "❄")
        self.assertEqual(weather_code_icon("FG"), "≋")
        self.assertEqual(weather_code_icon("TSRA"), "⚡")
        for raw in ("RA", "SN", "FZRA", "FG", "TSRA"):
            self.assertNotEqual(weather_code_icon(raw), raw)

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
            self.assertIn("OSM-подложка не получена", data.get("overlay_footer", ""))


if __name__ == "__main__":
    unittest.main()
