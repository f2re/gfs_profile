from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import xarray as xr

from composite_map import _area_subset_url, _field, _overlay_cache_path, _overpass_query, _storm_grid, _xy_km, area_box_from_radius, extract_overlay_shapes, load_basemap_outlines, load_overlay, summarize_overlay, weather_code_icon, write_composite_map_png
from geocode import GeoPoint
from gfs_core import GfsRun


class FakeOverpassResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.content = str(payload).encode("utf-8")

    def json(self) -> dict:
        return self.payload


def synthetic_overlay_elements() -> list[dict]:
    return [
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
    ]


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

    def test_overpass_parser_falls_back_to_way_geometry_for_city_label(self) -> None:
        overlay = {
            "_meta": {"status": "ok", "basemap": "places", "element_counts": {}},
            "elements": [
                {
                    "type": "way",
                    "id": 10,
                    "tags": {"place": "town", "name": "Городок"},
                    "geometry": [{"lat": 45.0, "lon": 39.0}, {"lat": 45.02, "lon": 39.02}],
                }
            ],
        }
        shapes = extract_overlay_shapes(overlay, 45.0, 39.0, 100.0)
        self.assertEqual(shapes["city_points"][0][0], "Городок")

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

    def test_load_overlay_records_response_metadata_and_parser_counts(self) -> None:
        box = (44.0, 46.0, 38.0, 40.0)
        with tempfile.TemporaryDirectory() as tmp, patch("composite_map.CACHE_DIR", Path(tmp)), patch("composite_map.requests.post") as post:
            post.return_value = FakeOverpassResponse({"elements": synthetic_overlay_elements()})
            overlay = load_overlay(box, "roads")
        meta = overlay["_meta"]
        self.assertEqual(meta["status"], "ok")
        self.assertFalse(meta["cache_hit"])
        self.assertEqual(meta["http_status"], 200)
        self.assertGreater(meta["response_bytes"], 0)
        self.assertEqual(meta["parsed_counts"], {"cities": 1, "water": 1, "rivers": 1, "roads": 1})
        self.assertEqual(len(meta["attempts"]), 1)

    def test_load_overlay_uses_v2_success_cache_metadata(self) -> None:
        box = (44.0, 46.0, 38.0, 40.0)
        with tempfile.TemporaryDirectory() as tmp, patch("composite_map.CACHE_DIR", Path(tmp)):
            with patch("composite_map.requests.post") as post:
                post.return_value = FakeOverpassResponse({"elements": synthetic_overlay_elements()})
                first = load_overlay(box, "places")
                self.assertFalse(first["_meta"]["cache_hit"])
            with patch("composite_map.requests.post") as post:
                second = load_overlay(box, "places")
                post.assert_not_called()
        self.assertTrue(second["_meta"]["cache_hit"])
        self.assertEqual(second["_meta"]["cache_version"], 2)
        self.assertEqual(second["_meta"]["parsed_counts"]["cities"], 1)

    def test_load_overlay_ignores_empty_or_old_cache_and_does_not_cache_empty(self) -> None:
        box = (44.0, 46.0, 38.0, 40.0)
        with tempfile.TemporaryDirectory() as tmp, patch("composite_map.CACHE_DIR", Path(tmp)), patch("composite_map.requests.post") as post:
            cache_path = _overlay_cache_path(box, "places")
            cache_path.write_text('{"elements": [], "_meta": {"status": "empty"}}', encoding="utf-8")
            post.return_value = FakeOverpassResponse({"elements": []})
            overlay = load_overlay(box, "places")
            self.assertEqual(post.call_count, 3)
            self.assertEqual(overlay["_meta"]["status"], "unavailable")
            self.assertFalse(overlay["_meta"]["cache_hit"])
            self.assertEqual(len(overlay["_meta"]["attempts"]), 3)
            self.assertEqual(cache_path.read_text(encoding="utf-8"), '{"elements": [], "_meta": {"status": "empty"}}')
        with tempfile.TemporaryDirectory() as tmp, patch("composite_map.CACHE_DIR", Path(tmp)), patch("composite_map.requests.post") as post:
            post.return_value = FakeOverpassResponse({"elements": []})
            overlay = load_overlay(box, "places")
            self.assertEqual(overlay["_meta"]["status"], "unavailable")
            self.assertFalse(_overlay_cache_path(box, "places").exists())

    def test_basemap_outlines_report_missing_cartopy_dependency(self) -> None:
        original_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "cartopy.io":
                raise ImportError("cartopy unavailable")
            return original_import(name, globals, locals, fromlist, level)

        with tempfile.TemporaryDirectory() as tmp, patch("composite_map.CACHE_DIR", Path(tmp)), patch("builtins.__import__", side_effect=fake_import):
            outlines = load_basemap_outlines((44.0, 46.0, 38.0, 40.0), "10m")
        self.assertEqual(outlines["_meta"]["status"], "missing_dependency")
        self.assertIn("cartopy", outlines["_meta"]["error"])

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
            outlines = {
                "_meta": {"status": "ok", "resolution": "10m", "line_count": 1, "point_count": 2},
                "features": [{"name": "coastlines", "lines": [[[44.9, 38.9], [45.1, 39.1]]]}],
            }
            out = write_composite_map_png(data, path, pixel_size=480, overlay={"elements": []}, basemap_outlines=outlines)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 10_000)
            self.assertIn("OSM-подложка не получена", data.get("overlay_footer", ""))
            self.assertEqual(data["basemap_outline_summary"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
