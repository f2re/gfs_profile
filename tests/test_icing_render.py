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
from icing_render import render_icing_png, _unknown_mask
from icing_presentation import display_icing_field, water_profile_missing


def small_fields():
    shape = (3, 2, 3)
    fields = compute_icing_fields(
        [1000, 850, 700], np.full(shape, 263.15), np.full(shape, 0.0015),
        np.broadcast_to(np.array([0, 1500, 3000])[:, None, None], shape),
        np.full(shape, 1e-4), np.full((2, 3), 101000.0), np.full((2, 3), -10.0),
    )
    lat, lon = np.meshgrid([55.0, 56.0], [35.0, 37.0, 39.0], indexing="ij")
    return fields, lat, lon


def water_coverage_fields():
    # Columns: a complete warm profile, only a valid warm bottom, and two cold
    # levels with an unavailable top. All requested levels are above ground.
    temperature = np.array([[280., 280., 268.], [285., np.nan, 267.], [290., np.nan, np.nan]])
    temperature = np.repeat(temperature[:, None, :], 2, axis=1)
    shape = temperature.shape
    fields = compute_icing_fields(
        [1000, 850, 700], temperature, np.full(shape, .0015),
        np.broadcast_to(np.array([0, 1500, 3000])[:, None, None], shape),
        np.full(shape, 1e-4), np.full((2, 3), 101000.), np.full((2, 3), -10.),
    )
    lat, lon = np.meshgrid([55., 56.], [35., 37., 39.], indexing="ij")
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

    def test_selected_point_is_rendered_and_preserved_in_png_metadata(self):
        fields, lat, lon = small_fields()
        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False):
            target = Path(directory) / "selected.png"
            render_icing_png(fields, lat, lon, "slw_max", run=GfsRun("20261117", "06"), lead=24,
                             bbox=(34, 40, 54, 57), output_path=target, point=(55.75, 37.62))
            with Image.open(target) as image:
                self.assertIn("selected point 55.7500,37.6200", image.info["Description"])
        with self.assertRaisesRegex(ValueError, "вне района"):
            render_icing_png(fields, lat, lon, "slw_max", run=GfsRun("20261117", "06"), lead=24,
                             bbox=(34, 40, 54, 57), output_path="unused.png", point=(10, 37.62))

    def test_thickness_values_and_contours_are_scaled_to_decametres(self):
        from matplotlib.axes import Axes
        fields, lat, lon = small_fields()
        metres = np.array([[0., 1000., 2000.], [0., 1000., 2000.]])
        fields["godske20_sum_gpm"] = metres.copy()
        contour_calls = []
        original = Axes.contour

        def record_contour(axis, *args, **kwargs):
            contour_calls.append((np.asarray(args[2]).copy(), np.asarray(kwargs["levels"]).copy()))
            return original(axis, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False), patch.object(Axes, "contour", new=record_contour):
            target = Path(directory) / "dam.png"
            render_icing_png(fields, lat, lon, "godske_sum", run=GfsRun("20261117", "06"), lead=24,
                             bbox=(34, 40, 54, 57), output_path=target, contour_step=100)
            np.testing.assert_array_equal(contour_calls[0][0], metres / 10)
            np.testing.assert_array_equal(contour_calls[0][1], np.arange(10, 201, 10))
            np.testing.assert_array_equal(fields["godske20_sum_gpm"], metres)
            with Image.open(target) as image:
                self.assertIn("; дам;", image.info["Description"])

    def test_default_region_uses_at_least_three_quarters_of_page_for_map(self):
        from matplotlib.figure import Figure
        fields, lat, lon = small_fields()
        positions = []
        original = Figure.savefig

        def capture_layout(figure, *args, **kwargs):
            positions.append(figure.axes[0].get_position().bounds)
            return original(figure, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False), patch.object(Figure, "savefig", new=capture_layout):
            render_icing_png(fields, lat, lon, "slw_max", run=GfsRun("20261117", "06"), lead=24,
                             bbox=(-15, 100, 34, 80), output_path=Path(directory) / "layout.png")
        self.assertGreaterEqual(positions[0][2] * positions[0][3], .75)

    def test_one_warm_level_does_not_make_missing_water_column_look_clear(self):
        fields, _, _ = water_coverage_fields()
        np.testing.assert_array_equal(water_profile_missing(fields)[0], [False, True, True])
        for product in ("slw_max", "slw_height"):
            values = display_icing_field(fields, product)
            unknown = _unknown_mask(fields, product, values, 850)
            np.testing.assert_array_equal(unknown[0], [False, True, False])
            self.assertTrue(np.isfinite(values[0, 2]))

    def test_partial_water_columns_are_hatched_for_integral_maximum_and_height(self):
        from matplotlib.figure import Figure
        fields, lat, lon = water_coverage_fields()
        recorded = []
        original = Figure.savefig

        def capture_quality(figure, *args, **kwargs):
            axis = figure.axes[0]
            recorded.append(([text.get_text() for text in axis.get_legend().get_texts()],
                             [hatch for collection in axis.collections
                              for hatch in (getattr(collection, "hatches", None) or [collection.get_hatch()])]))
            return original(figure, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False), patch.object(Figure, "savefig", new=capture_quality):
            for product in ("slwp", "slw_max", "slw_height"):
                render_icing_png(fields, lat, lon, product, run=GfsRun("20261117", "06"), lead=24,
                                 bbox=(34, 40, 54, 57), output_path=Path(directory) / (product + ".png"))
        for labels, hatches in recorded:
            self.assertIn("Неполные данные по высоте", labels)
            self.assertIn("//", hatches)
            self.assertIn("Нет данных для расчёта", labels)


if __name__ == "__main__":
    unittest.main()
