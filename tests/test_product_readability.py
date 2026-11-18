"""Prevent the overlapping labels found during visual menu-release acceptance."""
from __future__ import annotations

from datetime import datetime, timezone
import re
import unittest
from unittest.mock import patch

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from profile_plot_ru import write_profile_png
from windgram_plot import write_windgram_png
from windgram_product import WindgramData
from tests import test_formatters as profile_fixture
from tests import test_windgram_plot as wind_fixture
from tests import test_meteogram as meteogram_fixture
from meteogram_core import parse_deterministic_payload, source_for_id
from meteogram_plot_common import _draw_header


class ProductReadabilityTests(unittest.TestCase):
    def _capture_figure(self, render):
        figures = []
        savefig = Figure.savefig

        def capture(figure, *args, **kwargs):
            figures.append(figure)
            return savefig(figure, *args, **kwargs)

        with patch.object(Figure, "savefig", capture):
            path = render()
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertEqual(len(figures), 1)
        figure = figures[0]
        FigureCanvasAgg(figure)
        figure.canvas.draw()
        return figure

    def test_windgram_dates_have_a_separate_row_below_the_title(self):
        leads = list(range(0, 121, 6))
        data = WindgramData(
            run=wind_fixture.GfsRun("20260703", "00"),
            requested_lat=45.0, requested_lon=39.0, grid_lat=45.0, grid_lon=39.0,
            leads=leads, levels_hpa=[850, 700],
            cells=[wind_fixture.cell(lead, level, 1500.0 if level == 850 else 3000.0)
                   for level in (850, 700) for lead in leads], param="wind",
        )
        figure = self._capture_figure(lambda: write_windgram_png(data))
        axis = figure.axes[0]
        renderer = figure.canvas.get_renderer()
        title_box = axis.title.get_window_extent(renderer)
        dates = [text for text in axis.texts if re.fullmatch(r"\d{2}\.\d{2}", text.get_text())]
        self.assertTrue(dates)
        self.assertEqual(len(dates), len({text.get_text() for text in dates}))
        for text in dates:
            box = text.get_window_extent(renderer)
            self.assertLess(box.y1, title_box.y0)
            self.assertGreaterEqual(box.y0, axis.bbox.y0)
            self.assertLessEqual(box.y1, axis.bbox.y1)

    def test_profile_summary_stays_outside_wind_panel(self):
        data = profile_fixture.FormatterTests()._result()
        figure = self._capture_figure(lambda: write_profile_png(data))
        renderer = figure.canvas.get_renderer()
        summaries = [text for text in figure.texts if "Vmax" in text.get_text()]
        self.assertEqual(len(summaries), 1)
        self.assertLess(summaries[0].get_window_extent(renderer).y1, figure.axes[-1].bbox.y0)
        wind_texts = [text.get_window_extent(renderer) for text in figure.axes[-1].texts]
        for index, first in enumerate(wind_texts):
            for second in wind_texts[index + 1:]:
                self.assertFalse(first.overlaps(second))

    def test_meteogram_header_keeps_a_published_cycle(self):
        series = parse_deterministic_payload(
            meteogram_fixture._deterministic_payload(24), source=source_for_id("gfs"),
            point_label="Москва", requested_lat=55.75, requested_lon=37.62,
        )
        series.init_time_utc = datetime(2026, 8, 6, 0, tzinfo=timezone.utc)
        figure = plt.figure()
        self.addCleanup(plt.close, figure)
        _draw_header(figure, series, [])
        header = " ".join(text.get_text() for text in figure.texts)
        self.assertIn("цикл 06.08.2026 00 UTC", header)
        self.assertNotIn("цикл не указан", header)
