from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import debug_map_overlay


@dataclass
class FakeLayerStatus:
    ok: bool
    path: Path | None = None
    error: str | None = None


@dataclass
class FakeCacheStatus:
    resolution: str
    ready: bool
    data_dir: Path
    manifest_path: Path
    layers: dict
    missing_layers: list[str]
    failed_layers: dict


class DebugMapOverlayCliTests(unittest.TestCase):
    def test_debug_cli_smoke_outputs_local_basemap_fields(self) -> None:
        overlay = {
            "stats": {
                "status": "ok",
                "coastline_count": 1,
                "water_count": 2,
                "river_count": 3,
                "admin_count": 4,
                "road_count": 0,
                "city_count": 5,
                "warnings": ["слой roads отсутствует в локальном кэше"],
            }
        }
        cache = FakeCacheStatus(
            resolution="10m",
            ready=True,
            data_dir=Path("/tmp/basemap/natural_earth/10m"),
            manifest_path=Path("/tmp/basemap/natural_earth/10m/manifest.json"),
            layers={"coastline": FakeLayerStatus(True, Path("/tmp/coastline.shp"))},
            missing_layers=[],
            failed_layers={},
        )
        stdout = io.StringIO()
        with patch("debug_map_overlay.check_basemap_cache", return_value=cache), patch("debug_map_overlay.local_basemap_overlay", return_value=overlay), redirect_stdout(stdout):
            code = debug_map_overlay.main(["Москва", "--basemap", "places"])
        text = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("bbox:", text)
        self.assertIn("basemap mode: places", text)
        self.assertIn("resolution: 10m", text)
        self.assertIn("cache ready: yes", text)
        self.assertIn("parsed coastline/water/river/admin/road/city counts: 1/2/3/4/0/5", text)
        self.assertIn("warnings: слой roads отсутствует", text)
        self.assertIn("final status: ok", text)


if __name__ == "__main__":
    unittest.main()
