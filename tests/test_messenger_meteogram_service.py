from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from geocode import GeoPoint
from meteogram_core import MeteogramSeries, source_for_id
from messenger.meteogram_service import (
    build_meteogram_product_result,
    normalize_meteogram_params,
    parse_meteogram_input,
)
from messenger.profile_service import cleanup_product_result


class MeteogramServiceTests(unittest.TestCase):
    def _series(self, source_id="gfs"):
        source = source_for_id(source_id)
        start = datetime(2026, 9, 5, 12)
        times = [start + timedelta(hours=i) for i in range(6)]
        return MeteogramSeries(
            source=source,
            point_label="Москва",
            requested_lat=55.75,
            requested_lon=37.62,
            grid_lat=55.7,
            grid_lon=37.6,
            timezone="Europe/Moscow",
            times=times,
            fields={"temperature_2m": np.arange(6, dtype=float)},
        )

    def test_default_parse_is_gfs_5_days_png(self):
        parsed = parse_meteogram_input("Москва")
        self.assertEqual((parsed.location_query, parsed.source_id, parsed.days, parsed.output_format), ("Москва", "gfs", 5, "png"))

    def test_explicit_ensemble_and_pdf(self):
        parsed = parse_meteogram_input("Москва ensemble=gefs days=10 format=pdf")
        self.assertEqual((parsed.source_id, parsed.days, parsed.output_format), ("gefs", 10, "pdf"))

    def test_params_validate_source_horizon(self):
        with self.assertRaises(Exception):
            normalize_meteogram_params({"source": "icon_global", "days": 10})

    def test_png_result_has_no_invented_cycle(self):
        point = GeoPoint(55.75, 37.62, "Москва", "test")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meteo.png"
            def write_png(series):
                path.write_bytes(b"png"); return path
            with (
                patch("messenger.meteogram_service.fetch_meteogram", return_value=self._series()),
                patch("messenger.meteogram_service.write_meteogram_png", side_effect=write_png),
            ):
                result = build_meteogram_product_result(point, "gfs", 5, "png")
            self.assertIsNone(result.metadata["cycle"])
            self.assertEqual(result.metadata["source_id"], "gfs")
            self.assertIn("не наблюдение", result.summary)
            self.assertIn("cycle не указывается", result.summary)
            self.assertEqual(result.attachments[0].kind, "image")
            cleanup_product_result(result)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
