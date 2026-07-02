from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import shapefile

from basemap_cache import basemap_manifest_path, check_basemap_cache, is_basemap_cache_ready, iter_layer_features, local_basemap_overlay


def write_polyline_layer(base: Path, parts: list[list[tuple[float, float]]], name: str = "line") -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    with shapefile.Writer(str(base), shapeType=shapefile.POLYLINE) as writer:
        writer.field("NAME", "C")
        writer.line(parts)
        writer.record(name)


def write_polygon_layer(base: Path, parts: list[list[tuple[float, float]]], name: str = "polygon") -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    with shapefile.Writer(str(base), shapeType=shapefile.POLYGON) as writer:
        writer.field("NAME", "C")
        writer.poly(parts)
        writer.record(name)


def write_point_layer(base: Path, points: list[tuple[float, float, str, int, int]]) -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    with shapefile.Writer(str(base), shapeType=shapefile.POINT) as writer:
        writer.field("NAME", "C")
        writer.field("SCALERANK", "N")
        writer.field("POP_MAX", "N")
        writer.field("ADM0CAP", "N")
        for lon, lat, name, rank, pop in points:
            writer.point(lon, lat)
            writer.record(name, rank, pop, 0)


def write_manifest(root: Path, resolution: str = "10m") -> None:
    manifest = root / "natural_earth" / resolution / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema_version": 1, "source": "Natural Earth", "resolution": resolution, "files": {}, "failed_layers": {}}), encoding="utf-8")


def layer_base(root: Path, layer_file: str, resolution: str = "10m") -> Path:
    return root / "natural_earth" / resolution / f"ne_{resolution}_{layer_file}"


class BasemapCacheTests(unittest.TestCase):
    def test_manifest_path_uses_env_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MAP_BASEMAP_DIR": tmp}):
            self.assertEqual(basemap_manifest_path("10m"), Path(tmp) / "natural_earth" / "10m" / "manifest.json")

    def test_cache_ready_false_and_missing_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MAP_BASEMAP_DIR": tmp}):
            status = check_basemap_cache("10m")
            self.assertFalse(status.ready)
            self.assertIn("coastline", status.missing_layers)
            self.assertFalse(is_basemap_cache_ready("10m"))

    def test_cache_ready_true_with_required_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MAP_BASEMAP_DIR": tmp}):
            root = Path(tmp)
            write_manifest(root)
            write_polyline_layer(layer_base(root, "coastline"), [[(38.9, 44.9), (39.1, 45.1)]])
            write_polygon_layer(layer_base(root, "lakes"), [[(38.9, 44.9), (39.0, 44.9), (39.0, 45.0), (38.9, 44.9)]])
            write_polyline_layer(layer_base(root, "rivers_lake_centerlines"), [[(38.8, 44.8), (39.2, 45.2)]])
            write_polyline_layer(layer_base(root, "admin_0_boundary_lines_land"), [[(38.7, 45.0), (39.3, 45.0)]])
            write_point_layer(layer_base(root, "populated_places"), [(39.0, 45.0, "Краснодар", 2, 1000000)])
            self.assertTrue(is_basemap_cache_ready("10m"))
            self.assertTrue(check_basemap_cache("10m").ready)

    def test_bbox_filtering_and_multipart_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MAP_BASEMAP_DIR": tmp}):
            root = Path(tmp)
            write_polyline_layer(
                layer_base(root, "rivers_lake_centerlines"),
                [[(38.8, 44.8), (39.2, 45.2)], [(50.0, 50.0), (51.0, 51.0)]],
            )
            features = list(iter_layer_features("rivers", (44.0, 46.0, 38.0, 40.0), "10m"))
            self.assertEqual(len(features), 2)
            self.assertEqual(len(features[0].points), 2)

    def test_local_overlay_reads_layers_and_reports_optional_roads_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MAP_BASEMAP_DIR": tmp, "MAP_BASEMAP_AUTO_DOWNLOAD": "0"}):
            root = Path(tmp)
            write_manifest(root)
            write_polyline_layer(layer_base(root, "coastline"), [[(38.9, 44.9), (39.1, 45.1)]])
            write_polygon_layer(layer_base(root, "lakes"), [[(38.9, 44.9), (39.0, 44.9), (39.0, 45.0), (38.9, 44.9)]])
            write_polyline_layer(layer_base(root, "rivers_lake_centerlines"), [[(38.8, 44.8), (39.2, 45.2)]])
            write_polyline_layer(layer_base(root, "admin_0_boundary_lines_land"), [[(38.7, 45.0), (39.3, 45.0)]])
            write_point_layer(layer_base(root, "populated_places"), [(39.0, 45.0, "Краснодар", 2, 1000000)])
            with patch("basemap_cache.requests.get") as get:
                overlay = local_basemap_overlay(45.0, 39.0, 100.0, "roads", "10m")
                get.assert_not_called()
            self.assertEqual(overlay["stats"]["status"], "ok")
            self.assertEqual(overlay["stats"]["city_count"], 1)
            self.assertIn("roads", overlay["stats"]["missing_layers"])


if __name__ == "__main__":
    unittest.main()
