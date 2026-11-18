from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from gfs_core import GfsRun
from icing_config import ICING_PRODUCTS
from icing_meteorology import compute_icing_fields
from icing_render import render_icing_png


def small_fields():
    shape = (3, 2, 3)
    fields = compute_icing_fields(
        [1000, 850, 700], np.full(shape, 263.15), np.full(shape, 0.0015),
        np.broadcast_to(np.array([0, 1500, 3000])[:, None, None], shape),
        np.full(shape, 1e-4), np.full((2, 3), 101000.0), np.full((2, 3), -10.0),
    )
    lat, lon = np.meshgrid([55.0, 56.0], [35.0, 37.0, 39.0], indexing="ij")
    return fields, lat, lon


class IcingRenderTests(unittest.TestCase):
    def test_products_render_without_network_with_provenance(self):
        fields, lat, lon = small_fields()
        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False), patch("requests.get", side_effect=AssertionError("renderer must remain offline")):
            for product in ICING_PRODUCTS:
                with self.subTest(product=product):
                    target = Path(directory) / (product + ".png")
                    render_icing_png(fields, lat, lon, product, run=GfsRun("20261117", "06"), lead=24, bbox=(34, 40, 54, 57), output_path=target, contour_step=100)
                    with Image.open(target) as image:
                        self.assertGreater(image.width, 1500)
                        self.assertGreater(image.height, 1000)
                        self.assertLess(image.width + image.height, 10000)
                        self.assertIn("2026-11-18T06:00:00", image.info["Description"])
                        self.assertIn("GFS data; model", image.info["Description"])

    def test_empty_field_at_tiny_equatorial_bbox_is_renderable(self):
        fields = compute_icing_fields(
            [850], np.array([[[np.nan]]]), np.array([[[np.nan]]]),
            np.array([[[1500.0]]]), np.array([[[np.nan]]]),
            np.array([[100000.0]]), np.array([[0.0]]),
        )
        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False):
            target = Path(directory) / "empty.png"
            render_icing_png(fields, np.array([[0.125]]), np.array([[0.125]]), "godske_sum", run=GfsRun("20261117", "06"), lead=0, bbox=(0, 0.25, 0, 0.25), output_path=target)
            self.assertGreater(target.stat().st_size, 10000)

    def test_rejects_coordinate_mismatch(self):
        fields, lat, lon = small_fields()
        with self.assertRaises(ValueError):
            render_icing_png(fields, lat[:1], lon, "slwp", run=GfsRun("20261117", "06"), lead=0, bbox=(34, 40, 54, 57), output_path="unused.png")


if __name__ == "__main__":
    unittest.main()
