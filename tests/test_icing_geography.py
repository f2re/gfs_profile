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
    WATER_COLOR, _water_patch, _label_extent, city_candidates, draw_cities, draw_selected_point,
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
        bbox = (-15, 100, 34, 80)
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
        self.assertTrue({"Москва", "Санкт-Петербург", "Краснодар", "Воронеж", "Пермь"}.issubset({label.get_text() for label in labels}))

    def test_selected_point_keeps_exact_coordinates_and_priority_over_labels(self):
        bbox = (-15, 100, 34, 80)
        project = _projector(bbox)
        outline = _boundary(bbox, project)
        for point in ((55.75, 37.62), (34, -15), (80, 100)):
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
                selected = draw_selected_point(axis, point, bbox, project, clip)
                cities = draw_cities(axis, bbox, project, clip)
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                selected_box = _label_extent(selected, renderer)
                self.assertIn("Выбранный пункт", selected.get_text())
                self.assertFalse(selected_box.overlaps(axis.get_legend().get_window_extent(renderer)))
                self.assertFalse(clip.get_path().transformed(clip.get_transform()).intersects_bbox(selected_box, filled=False))
                self.assertFalse(any(selected_box.overlaps(city.get_window_extent(renderer)) for city in cities))
                self.assertFalse(contour.get_visible())
                markers = [line for line in axis.lines if line.get_gid() == "icing_selected_point_marker"]
                self.assertEqual(len(markers), 2)
                for marker in markers:
                    np.testing.assert_allclose(marker.get_xdata(), [x])
                    np.testing.assert_allclose(marker.get_ydata(), [y])
                figure.clear()


if __name__ == "__main__":
    unittest.main()
