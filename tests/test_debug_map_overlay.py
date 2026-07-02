from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import debug_map_overlay


class DebugMapOverlayCliTests(unittest.TestCase):
    def test_debug_cli_smoke_outputs_required_fields(self) -> None:
        overlay = {
            "_meta": {
                "status": "ok",
                "basemap": "places",
                "endpoint": "https://example.test/api/interpreter",
                "endpoint_label": "example.test",
                "cache_hit": False,
                "http_status": 200,
                "response_bytes": 1234,
                "query_hash": "abc123",
                "attempts": [
                    {
                        "endpoint": "https://example.test/api/interpreter",
                        "endpoint_label": "example.test",
                        "http_status": 200,
                        "elapsed_ms": 42,
                        "response_bytes": 1234,
                        "elements": 1,
                    }
                ],
                "element_counts": {"raw_elements": 1},
                "parsed_counts": {"cities": 1, "water": 0, "rivers": 0, "roads": 0},
            },
            "elements": [{"type": "node", "id": 1, "lat": 55.7558, "lon": 37.6173, "tags": {"place": "city", "name": "Москва"}}],
        }
        stdout = io.StringIO()
        outlines = {
            "_meta": {
                "status": "ok",
                "resolution": "10m",
                "cache_hit": True,
                "feature_counts": {"coastlines": 1},
                "line_count": 1,
                "point_count": 2,
            },
            "features": [{"name": "coastlines", "lines": [[[55.7, 37.5], [55.8, 37.6]]]}],
        }
        with patch("debug_map_overlay.load_overlay", return_value=overlay), patch("debug_map_overlay.load_basemap_outlines", return_value=outlines), redirect_stdout(stdout):
            code = debug_map_overlay.main(["Москва", "--basemap", "places"])
        text = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("bbox:", text)
        self.assertIn("basemap mode: places", text)
        self.assertIn("cache path:", text)
        self.assertIn("endpoint attempts:", text)
        self.assertIn("HTTP status: 200", text)
        self.assertIn("response bytes: 1234", text)
        self.assertIn("raw elements count: 1", text)
        self.assertIn("parsed city/water/river/road counts: 1/0/0/0", text)
        self.assertIn("basemap outlines: status=ok", text)
        self.assertIn("final status: ok", text)


if __name__ == "__main__":
    unittest.main()
