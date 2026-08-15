from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from meteogram_models import MeteogramSeries, source_for_id
from meteogram_plot_weather import _draw_precipitation
from meteogram_precip_style import add_precipitation_upper_layer
from telegram_meteogram import _output_keyboard


class MeteogramGfsAndPrecipitationTests(unittest.TestCase):
    def _series(self, source_id: str, *, ensemble: bool = False) -> MeteogramSeries:
        times = [
            datetime(2026, 8, 15, tzinfo=timezone.utc) + timedelta(hours=3 * index)
            for index in range(17)
        ]
        intensity = np.array(
            [0.0, 0.05, 0.2, 0.7, 1.8, 3.2, 0.6, 0.0, 0.1, 0.4, 2.4, 5.5, 1.0, 0.0, 0.2, 0.8, 0.0],
            dtype=float,
        )
        precipitation = intensity * 3.0
        fields = {
            "precipitation_intensity": intensity,
            "precipitation": precipitation,
            "weather_code": np.full(len(times), 3.0),
        }
        stats: dict[str, dict[str, np.ndarray]] = {}
        member_count = None
        expected_member_count = None
        if ensemble:
            q90_intensity = intensity * 1.8
            q90 = precipitation * 1.8
            stats["precipitation"] = {
                "q50": precipitation,
                "q50_intensity": intensity,
                "q90": q90,
                "q90_intensity": q90_intensity,
                "members": np.vstack((precipitation * 0.7, precipitation, q90)),
            }
            fields.update(
                {
                    "precipitation_probability_0p1": np.where(precipitation > 0, 80.0, 0.0),
                    "precipitation_probability_1": np.where(precipitation >= 1.0, 55.0, 0.0),
                    "precipitation_probability_5": np.where(precipitation >= 5.0, 25.0, 0.0),
                }
            )
            member_count = 3
            expected_member_count = 3
        return MeteogramSeries(
            source=source_for_id(source_id),
            point_label="Санкт-Петербург",
            requested_lat=59.939,
            requested_lon=30.316,
            grid_lat=59.94,
            grid_lon=30.32,
            timezone="UTC",
            times=times,
            fields=fields,
            stats=stats,
            member_count=member_count,
            expected_member_count=expected_member_count,
        )

    def test_gfs_surface_source_uses_seamless_domain(self):
        source = source_for_id("gfs")
        self.assertEqual(source.endpoint, "https://api.open-meteo.com/v1/gfs")
        self.assertEqual(source.upstream_id, "gfs_seamless")
        self.assertIn("NOAA/NCEP", source.label)
        self.assertNotIn("0.25°", source.label)

    def test_output_keyboard_contains_png_docx_and_pdf(self):
        callbacks = {
            button.callback_data
            for row in _output_keyboard().inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith("meteo:format:")
        }
        self.assertEqual(
            callbacks,
            {"meteo:format:png", "meteo:format:docx", "meteo:format:pdf"},
        )

    def test_deterministic_precipitation_marks_daily_maximum(self):
        series = self._series("gfs")
        figure, axis = plt.subplots(figsize=(8, 3))
        try:
            x = mdates.date2num(series.times)
            _draw_precipitation(axis, x, series, [])
            add_precipitation_upper_layer(axis, x, series)
            base_bars = list(axis._meteogram_precipitation_bars)
            max_bars = list(axis._meteogram_precipitation_max_bars)
            self.assertTrue(base_bars)
            self.assertTrue(max_bars)
            self.assertEqual(axis._meteogram_precipitation_max_label, "макс. за сутки")
            self.assertTrue(all(bar.get_hatch() == "////" for bar in max_bars))
            self.assertTrue(all(bar.get_facecolor()[-1] < 0.3 for bar in max_bars))
            self.assertTrue(all(bar.get_facecolor()[-1] < 1.0 for bar in base_bars))
            self.assertGreater(base_bars[1].get_height(), 0.0)
        finally:
            plt.close(figure)

    def test_ensemble_precipitation_uses_p90_not_fake_maximum(self):
        series = self._series("gefs", ensemble=True)
        figure, axis = plt.subplots(figsize=(8, 3))
        try:
            x = mdates.date2num(series.times)
            _draw_precipitation(axis, x, series, [])
            add_precipitation_upper_layer(axis, x, series)
            max_bars = list(axis._meteogram_precipitation_max_bars)
            self.assertTrue(max_bars)
            self.assertEqual(axis._meteogram_precipitation_max_label, "верхняя оценка P90")
            self.assertTrue(all(bar.get_hatch() == "////" for bar in max_bars))
            probability_axis = axis._meteogram_probability_axis
            self.assertIsNotNone(probability_axis)
            self.assertTrue(all(line.get_linestyle() == "--" for line in probability_axis.lines))
        finally:
            plt.close(figure)


if __name__ == "__main__":
    unittest.main()
