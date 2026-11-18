from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.path import Path
from matplotlib.patches import PathPatch, Patch

from icing_geography import (
    WATER_COLOR, SELECTED_COLOR, _water_patch, city_candidates, draw_cities, draw_selected_point,
)
from icing_render import _boundary, _projector


class IcingGeographyTests(unittest.TestCase):
    def test_ocean_holes_do_not_fill_islands_or_land(self):
        figure = Figure(figsize=(4, 4), dpi=100, facecolor="white")
        canvas = FigureCanvasAgg(figure)
        axis = figure.add_axes((0, 0, 1, 1))
        axis.set_xlim(0, 10); axis.set_ylim(0, 10)
        axis.set_axis_off()
        clip = PathPatch(Path([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]), facecolor="white", zorder=0)
        axis.add_patch(clip)
        outer = [(-1, -1), (-1, 11), (11, 11), (11, -1), (-1, -1)]
        hole = [(2, 2), (8, 2), (8, 8), (2, 8), (2, 2)]
        _water_patch(axis, [outer, hole], (0, 10, 0, 10), lambda x, y: (x, y), clip)
        canvas.draw()
        pixels = np.asarray(canvas.buffer_rgba())
        np.testing.assert_array_equal(pixels[200, 200, :3], [255, 255, 255])
        np.testing.assert_array_equal(pixels[20, 20, :3], np.round(np.array(to_rgb(WATER_COLOR)) * 255))

    def test_all_major_russian_cities_are_available_offline(self):
        rows = city_candidates((-15, 100, 34, 80))
        names = {row[0] for row in rows if row[4] == 0}
        self.assertEqual(len(names), 16)
        self.assertTrue({"Краснодар", "Воронеж", "Санкт-Петербург", "Пермь", "Нижний Новгород"}.issubset(names))

    def test_city_labels_do_not_overlap_or_extend_outside_map(self):
        bbox = (12, 85, 40.5, 70.5)
        project = _projector(bbox)
        outline = _boundary(bbox, project)
        figure = Figure(figsize=(12.8, 8), dpi=170)
        FigureCanvasAgg(figure)
        axis = figure.add_axes((.01, .01, .98, .98))
        axis.set_aspect("equal")
        axis.set_xlim(outline[:, 0].min(), outline[:, 0].max())
        axis.set_ylim(outline[:, 1].min(), outline[:, 1].max())
        clip = PathPatch(Path(outline), facecolor="white")
        axis.add_patch(clip)
        with patch("requests.get", side_effect=AssertionError("city rendering must remain offline")):
            labels = draw_cities(axis, bbox, project, clip)
        self.assertGreater(len(labels), 25)
        renderer = figure.canvas.get_renderer()
        bounds = [label.get_window_extent(renderer) for label in labels]
        for i, box in enumerate(bounds):
            self.assertFalse(any(box.overlaps(other) for other in bounds[i + 1:]))
        self.assertTrue({"Москва", "Санкт-Петербург", "Краснодар", "Воронеж", "Пермь", "Берлин", "Мурманск", "Новосибирск"}.issubset({label.get_text() for label in labels}))
        map_outline = clip.get_path().transformed(clip.get_transform())
        for box in bounds:
            self.assertTrue(map_outline.contains_points(box.corners()).all())

    def test_selected_point_is_small_marker_only_at_exact_coordinates(self):
        bbox = (12, 85, 40.5, 70.5)
        project = _projector(bbox)
        outline = _boundary(bbox, project)
        marker_pixel_counts = []
        for point in ((55.75, 37.62), (40.5, 12), (70.5, 85), (70.5, 12), (40.5, 85)):
            with self.subTest(point=point):
                figure = Figure(figsize=(12.8, 8), dpi=170)
                FigureCanvasAgg(figure)
                axis = figure.add_axes((.01, .01, .98, .98))
                axis.set_aspect("equal")
                axis.set_xlim(outline[:, 0].min(), outline[:, 0].max())
                axis.set_ylim(outline[:, 1].min(), outline[:, 1].max())
                clip = PathPatch(Path(outline), facecolor="white")
                axis.add_patch(clip)
                axis.legend(handles=[Patch(facecolor="gray", label="Нет данных")], loc="lower left")
                x, y = project(point[1], point[0])
                contour = axis.text(x, y, "150", gid="icing_contour")
                original_texts = list(axis.texts)
                markers = draw_selected_point(axis, point, bbox, project, clip)
                self.assertEqual(list(axis.texts), original_texts)
                cities = draw_cities(axis, bbox, project, clip)
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                self.assertFalse(contour.get_visible())
                self.assertEqual(len(markers), 2)
                for marker in markers:
                    np.testing.assert_allclose(marker.get_xdata(), [x])
                    np.testing.assert_allclose(marker.get_ydata(), [y])
                    self.assertLessEqual(marker.get_markersize(), 11)
                    marker_box = marker.get_window_extent(renderer)
                    self.assertFalse(marker_box.overlaps(axis.get_legend().get_window_extent(renderer)))
                    self.assertFalse(any(marker_box.overlaps(city.get_window_extent(renderer)) for city in cities))
                self.assertFalse(any(text.get_gid() == "icing_selected_point_label" for text in axis.texts))
                pixels = np.asarray(figure.canvas.buffer_rgba())[:, :, :3]
                marker_rgb = np.round(np.asarray(to_rgb(SELECTED_COLOR)) * 255).astype(np.uint8)
                marker_pixel_counts.append(int(np.all(pixels == marker_rgb, axis=-1).sum()))
                figure.clear()
        self.assertGreater(min(marker_pixel_counts), 0.85 * max(marker_pixel_counts))


if __name__ == "__main__":
    unittest.main()
