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
        cloud_fraction_pct=np.full(shape, 100.0), vertical_velocity_pas=np.zeros(shape),
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
        cloud_fraction_pct=np.full(shape, 100.0), vertical_velocity_pas=np.zeros(shape),
    )
    lat, lon = np.meshgrid([55., 56.], [35., 37., 39.], indexing="ij")
    return fields, lat, lon


class IcingRenderTests(unittest.TestCase):
    def test_composite_uses_index_and_same_layer_outline_with_distinct_quality_marks(self):
        from matplotlib.figure import Figure
        from matplotlib.colors import Normalize, BoundaryNorm
        fields, lat, lon = small_fields()
        fields["composite_index"] = np.array([[15., 75., np.nan], [0., 55., 40.]])
        fields["cloud_godske_sum_gpm"] = np.array([[0., 500., 0.], [0., 500., 0.]])
        fields["godske20_sum_gpm"][:] = 1000.  # A raw signal must not draw the outline.
        fields["composite_profile_missing"] = np.array([[False, True, True], [False, False, False]])
        fields["cloud_input_conflict"] = np.array([[True, False, False], [False, False, False]])
        original = Figure.savefig

        def capture_composite(figure, *args, **kwargs):
            axis = figure.axes[0]
            colour = axis.collections[0]
            self.assertIsInstance(colour.norm, Normalize)
            self.assertNotIsInstance(colour.norm, BoundaryNorm)
            self.assertEqual(colour.get_clim(), (0., 100.))
            self.assertEqual(np.ma.max(colour.get_array()), 75.)
            self.assertEqual(int(np.ma.getmaskarray(colour.get_array()).sum()), 2)
            outlines = [item for item in axis.collections if item.get_gid() == "icing_same_layer_godske"]
            self.assertEqual(len(outlines), 1)
            labels = [text.get_text() for text in axis.get_legend().get_texts()]
            self.assertIn("Облачные слои по Годске", labels)
            self.assertIn("Вода без облачности модели", labels)
            self.assertIn("Неполные данные по высоте", labels)
            self.assertIn("Нет данных для расчёта", labels)
            hatches = [hatch for collection in axis.collections
                       for hatch in (getattr(collection, "hatches", None) or [collection.get_hatch()])]
            self.assertIn("//", hatches)
            self.assertIn("\\\\", hatches)
            self.assertNotIn("%", " ".join(text.get_text() for text in figure.texts))
            return original(figure, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False), \
                patch.object(Figure, "savefig", new=capture_composite):
            render_icing_png(fields, lat, lon, "composite", run=GfsRun("20261117", "06"), lead=24,
                             bbox=(34, 40, 54, 57), output_path=Path(directory) / "composite.png")

    def test_level_map_does_not_show_conflict_from_another_height(self):
        from matplotlib.figure import Figure
        fields, lat, lon = small_fields()
        fields["cloud_input_conflict"][:] = True
        fields["cloud_input_conflict_level"][:] = False
        fields["cloud_input_conflict_level"][0] = True
        original = Figure.savefig

        def capture_level(figure, *args, **kwargs):
            legend = figure.axes[0].get_legend()
            labels = [text.get_text() for text in legend.get_texts()] if legend is not None else []
            self.assertNotIn("Вода без облачности модели", labels)
            return original(figure, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False), \
                patch.object(Figure, "savefig", new=capture_level):
            render_icing_png(fields, lat, lon, "slw_level", level_hpa=850,
                             run=GfsRun("20261117", "06"), lead=24, bbox=(34, 40, 54, 57),
                             output_path=Path(directory) / "level.png")

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

    def test_selected_marker_stays_visible_over_empty_field_message(self):
        from matplotlib.colors import to_rgb
        from icing_geography import SELECTED_COLOR
        fields = compute_icing_fields(
            [850], np.array([[[np.nan]]]), np.array([[[np.nan]]]),
            np.array([[[1500.0]]]), np.array([[[np.nan]]]),
            np.array([[100000.0]]), np.array([[0.0]]),
        )
        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False):
            target = Path(directory) / "empty_with_point.png"
            render_icing_png(fields, np.array([[0.125]]), np.array([[0.125]]), "godske_sum",
                             run=GfsRun("20261117", "06"), lead=0, bbox=(0, 0.25, 0, 0.25),
                             point=(0.125, 0.125), output_path=target)
            with Image.open(target) as image:
                pixels = np.asarray(image.convert("RGB"))
                marker_rgb = np.round(np.asarray(to_rgb(SELECTED_COLOR)) * 255).astype(np.uint8)
                self.assertGreater(int(np.all(pixels == marker_rgb, axis=-1).sum()), 30)

    def test_rejects_coordinate_mismatch(self):
        fields, lat, lon = small_fields()
        with self.assertRaises(ValueError):
            render_icing_png(fields, lat[:1], lon, "slwp", run=GfsRun("20261117", "06"), lead=0, bbox=(34, 40, 54, 57), output_path="unused.png")

    def test_selected_point_is_rendered_and_preserved_in_png_metadata(self):
        from matplotlib.figure import Figure
        fields, lat, lon = small_fields()
        original = Figure.savefig

        def capture_point_layout(figure, *args, **kwargs):
            figure.canvas.draw()
            renderer = figure.canvas.get_renderer()
            axis = figure.axes[0]
            self.assertFalse(any("Выбранный пункт" in text.get_text() for text in axis.texts))
            coordinate_notes = [text for text in figure.texts if "Пункт:" in text.get_text()]
            self.assertEqual(len(coordinate_notes), 1)
            self.assertIn("55,7500° с.ш., 37,6200° в.д.", coordinate_notes[0].get_text())
            note_box = coordinate_notes[0].get_window_extent(renderer)
            self.assertLess(note_box.y1, axis.get_window_extent(renderer).y0)
            other_footer = [text for text in figure.texts if text is not coordinate_notes[0]
                            and text.get_position()[1] == coordinate_notes[0].get_position()[1]]
            self.assertFalse(any(note_box.overlaps(text.get_window_extent(renderer)) for text in other_footer))
            return original(figure, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch("icing_render._basemap", return_value=False), patch.object(Figure, "savefig", new=capture_point_layout):
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
        fields["cloud_godske_sum_gpm"] = metres.copy()
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
            np.testing.assert_array_equal(fields["cloud_godske_sum_gpm"], metres)
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
                             bbox=(12, 85, 40.5, 70.5), output_path=Path(directory) / "layout.png")
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
