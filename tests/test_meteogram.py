from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from meteogram_core import (
    MeteogramError,
    _validate_payload_units,
    fetch_meteogram,
    parse_deterministic_payload,
    parse_ensemble_payload,
    source_for_id,
    validate_days,
)
from meteogram_plot import (
    PRECIPITATION_RATE_CAP_MM_H,
    TRACE_RATE_LIMIT_MM_H,
    _resolve_overlaps,
    audit_meteogram_layout,
    build_meteogram_figure,
    write_meteogram_png,
)
from meteogram_request import parse_meteogram_request


def _times(hours: int = 96) -> list[str]:
    start = datetime(2026, 8, 6, tzinfo=timezone.utc)
    return [(start + timedelta(hours=index * 3)).strftime("%Y-%m-%dT%H:%M") for index in range(hours // 3 + 1)]


def _deterministic_payload(hours: int = 120) -> dict:
    times = _times(hours)
    count = len(times)
    phase = np.linspace(0, 4 * np.pi, count)
    return {
        "latitude": 55.75,
        "longitude": 37.625,
        "timezone": "Europe/Moscow",
        "hourly_units": {
            "temperature_2m": "°C",
            "dew_point_2m": "°C",
            "relative_humidity_2m": "%",
            "precipitation": "mm",
            "pressure_msl": "hPa",
            "wind_speed_10m": "m/s",
            "wind_gusts_10m": "m/s",
            "wind_direction_10m": "°",
        },
        "hourly": {
            "time": times,
            "temperature_2m": (18 + 7 * np.sin(phase)).tolist(),
            "dew_point_2m": (11 + 4 * np.sin(phase - 0.4)).tolist(),
            "relative_humidity_2m": np.clip(70 - 20 * np.sin(phase), 20, 100).tolist(),
            "precipitation": np.where(np.sin(phase * 1.7) > 0.75, 3.2, 0.0).tolist(),
            "pressure_msl": (1012 + 7 * np.cos(phase / 2)).tolist(),
            "cloud_cover": np.clip(50 + 45 * np.sin(phase * 1.2), 0, 100).tolist(),
            "cloud_cover_low": np.clip(30 + 35 * np.sin(phase), 0, 100).tolist(),
            "cloud_cover_mid": np.clip(40 + 30 * np.cos(phase), 0, 100).tolist(),
            "cloud_cover_high": np.clip(35 + 30 * np.sin(phase / 2), 0, 100).tolist(),
            "wind_speed_10m": (5 + 3 * np.sin(phase / 2) ** 2).tolist(),
            "wind_gusts_10m": (8 + 5 * np.sin(phase / 2) ** 2).tolist(),
            "wind_direction_10m": ((210 + np.arange(count) * 9) % 360).tolist(),
            "weather_code": [3] * count,
            "is_day": [1 if 7 <= (index * 3) % 24 < 19 else 0 for index in range(count)],
        },
    }


def _ensemble_payload(hours: int = 240, members: int = 5) -> dict:
    base = _deterministic_payload(hours)
    hourly = {"time": base["hourly"]["time"]}
    for member in range(members):
        offset = member - (members - 1) / 2
        for name in (
            "temperature_2m", "dew_point_2m", "relative_humidity_2m",
            "precipitation", "pressure_msl", "cloud_cover",
            "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "weather_code",
        ):
            values = np.asarray(base["hourly"][name], dtype=float)
            if name == "wind_direction_10m":
                values = (values + offset * 4) % 360
            elif name == "precipitation":
                values = np.maximum(0, values + max(0, offset) * 0.4)
            elif name != "weather_code":
                values = values + offset * 0.35
            hourly[f"{name}_member{member:02d}"] = values.tolist()
    return {
        "latitude": 55.75,
        "longitude": 37.625,
        "timezone": "Europe/Moscow",
        "hourly_units": dict(base["hourly_units"]),
        "hourly": hourly,
    }


class MeteogramRequestTests(unittest.TestCase):
    def test_parse_deterministic(self):
        request = parse_meteogram_request("Москва source=ecmwf_ifs days=5")
        self.assertEqual(request.location_query, "Москва")
        self.assertEqual(request.source_id, "ecmwf_ifs")
        self.assertEqual(request.days, 5)

    def test_parse_ensemble_alias(self):
        request = parse_meteogram_request("Москва ensemble=ecmwf days=10")
        self.assertEqual(request.source_id, "ecmwf_ens")

    def test_horizon_rejected(self):
        with self.assertRaises(MeteogramError):
            validate_days(source_for_id("icon_global"), 15)


class MeteogramDataTests(unittest.TestCase):
    def test_deterministic_parse(self):
        series = parse_deterministic_payload(
            _deterministic_payload(), source=source_for_id("gfs"), point_label="Москва",
            requested_lat=55.75, requested_lon=37.62,
        )
        self.assertGreater(len(series.times), 24)
        self.assertTrue(np.isfinite(series.values("temperature_2m")).all())
        self.assertTrue(np.nanmax(series.values("precipitation_intensity")) > 0)

    def test_ensemble_statistics_and_direction(self):
        series = parse_ensemble_payload(
            _ensemble_payload(), source=source_for_id("gefs"), point_label="Москва",
            requested_lat=55.75, requested_lon=37.62,
        )
        self.assertEqual(series.member_count, 5)
        self.assertEqual(series.expected_member_count, 31)
        self.assertTrue(np.isfinite(series.statistic("temperature_2m", "q10")).all())
        self.assertTrue(np.isfinite(series.values("wind_direction_10m")).all())
        self.assertIn("Неполный ансамбль", " ".join(series.warnings))

    def test_unit_mismatch_rejected(self):
        payload = _deterministic_payload()
        payload["hourly_units"]["wind_speed_10m"] = "km/h"
        with self.assertRaises(MeteogramError):
            _validate_payload_units(payload, source_for_id("gfs"))

    def test_non_increasing_times_rejected(self):
        payload = _deterministic_payload()
        payload["hourly"]["time"][2] = payload["hourly"]["time"][1]
        with self.assertRaises(MeteogramError):
            parse_deterministic_payload(
                payload, source=source_for_id("gfs"), point_label="Москва",
                requested_lat=55.75, requested_lon=37.62,
            )

    def test_invalid_response_is_not_cached(self):
        payload = _deterministic_payload(24)
        payload["hourly_units"]["wind_speed_10m"] = "km/h"
        with (
            patch("meteogram_fetch._read_cache", return_value=None),
            patch("meteogram_fetch._request_json", return_value=payload),
            patch("meteogram_fetch._write_cache") as write_cache,
        ):
            with self.assertRaises(MeteogramError):
                fetch_meteogram("gfs", "Москва", 55.75, 37.62, 1)
        write_cache.assert_not_called()

    def test_invalid_cache_is_reloaded(self):
        cached = _deterministic_payload(24)
        cached["hourly_units"]["wind_speed_10m"] = "km/h"
        fresh = _deterministic_payload(24)
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "meteogram.json"
            cache_path.write_text("{}", encoding="utf-8")
            with (
                patch("meteogram_fetch._cache_path", return_value=cache_path),
                patch("meteogram_fetch._read_cache", return_value=cached),
                patch("meteogram_fetch._request_json", return_value=fresh) as request_json,
                patch("meteogram_fetch._write_cache") as write_cache,
            ):
                series = fetch_meteogram("gfs", "Москва", 55.75, 37.62, 1)
        self.assertTrue(np.isfinite(series.values("temperature_2m")).all())
        request_json.assert_called_once()
        write_cache.assert_called_once()

    def test_ensemble_probabilities_and_per_time_coverage(self):
        payload = _ensemble_payload(members=7)
        payload["hourly"]["temperature_2m_member06"][4] = None
        series = parse_ensemble_payload(
            payload, source=source_for_id("gefs"), point_label="Москва",
            requested_lat=55.75, requested_lon=37.62,
        )
        self.assertEqual(series.member_count, 7)
        self.assertEqual(series.values("ensemble_member_count")[4], 6)
        self.assertTrue(np.isfinite(series.values("precipitation_probability_0p1")).all())
        self.assertTrue(np.isfinite(series.values("precipitation_probability_1")).all())
        self.assertTrue(np.isfinite(series.values("precipitation_probability_5")).all())


class MeteogramRenderTests(unittest.TestCase):
    def _render(self, series) -> dict[str, int]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meteogram.png"
            write_meteogram_png(series, path)
            self.assertTrue(path.exists())
            result = audit_meteogram_layout(path)
            self.assertLessEqual(result["dimension_sum"], 10000)
            return result

    def test_deterministic_png(self):
        series = parse_deterministic_payload(
            _deterministic_payload(120), source=source_for_id("ecmwf_ifs"), point_label="Санкт-Петербург",
            requested_lat=59.939, requested_lon=30.316,
        )
        self._render(series)

    def test_ensemble_png_long_period(self):
        series = parse_ensemble_payload(
            _ensemble_payload(360), source=source_for_id("ecmwf_ens"), point_label="Очень длинное название пункта для проверки компоновки",
            requested_lat=55.75, requested_lon=37.62,
        )
        result = self._render(series)
        self.assertGreater(result["width"], result["height"])

    def test_semantic_axes_and_legends(self):
        payload = _deterministic_payload(120)
        payload["hourly"]["precipitation"][1] = 0.15
        payload["hourly"]["weather_code"][5] = 95
        payload["hourly"]["precipitation"][5] = 6.0
        series = parse_deterministic_payload(
            payload, source=source_for_id("gfs"), point_label="Москва",
            requested_lat=55.75, requested_lon=37.62,
        )
        figure, axes, _tracked = build_meteogram_figure(series)
        try:
            figure.canvas.draw()
            precipitation_axis = axes[3]
            wind_axis = axes[4]
            self.assertEqual(precipitation_axis.get_ylim()[1], PRECIPITATION_RATE_CAP_MM_H)
            self.assertNotIn(0.0, precipitation_axis.get_yticks())
            self.assertIsNotNone(precipitation_axis._meteogram_trace_markers)
            self.assertLess(float(np.nanmin(series.values("precipitation_intensity"))), TRACE_RATE_LIMIT_MM_H)
            bars = list(precipitation_axis._meteogram_precipitation_bars)
            self.assertEqual(bars[0].get_edgecolor()[-1], 0.0)
            self.assertGreater(bars[5].get_edgecolor()[-1], 0.0)
            self.assertGreaterEqual(wind_axis.get_ylim()[1], 25.0)

            temperature_legend = axes[1].get_legend()
            self.assertIsNotNone(temperature_legend)
            renderer = figure.canvas.get_renderer()
            self.assertGreaterEqual(
                temperature_legend.get_window_extent(renderer).y0,
                axes[1].get_window_extent(renderer).y1 - 1.0,
            )
            precipitation_legend = precipitation_axis.get_legend()
            labels = precipitation_axis._meteogram_daily_labels
            if precipitation_legend is not None and labels:
                legend_box = precipitation_legend.get_window_extent(renderer)
                self.assertTrue(all(not legend_box.overlaps(label.get_window_extent(renderer)) for label in labels if label.get_visible()))
        finally:
            plt.close(figure)

    def test_daily_precipitation_text_and_wind_terms(self):
        payload = _deterministic_payload(48)
        count = len(payload["hourly"]["time"])
        payload["hourly"]["precipitation"] = [0.0] * count
        payload["hourly"]["weather_code"] = [3] * count
        for index in (1, 2, 3):
            payload["hourly"]["precipitation"][index] = 1.0
            payload["hourly"]["weather_code"][index] = 71
        payload["hourly"]["wind_speed_10m"] = [12.0] * count
        payload["hourly"]["wind_gusts_10m"] = [13.0] * count
        series = parse_deterministic_payload(
            payload, source=source_for_id("gfs"), point_label="Москва",
            requested_lat=55.75, requested_lon=37.62,
        )
        figure, axes, tracked = build_meteogram_figure(series)
        try:
            daily = [artist.get_text() for artist in axes[3]._meteogram_daily_labels]
            self.assertTrue(any("мм/сут" in text and "снег" in text for text in daily))
            tracked_text = [
                artist.get_text()
                for artist, _priority in tracked
                if hasattr(artist, "get_text")
            ]
            self.assertIn("сильный ветер", tracked_text)
            self.assertNotIn("сильные порывы", tracked_text)
        finally:
            plt.close(figure)

    def test_ensemble_probability_lines_and_safe_png(self):
        series = parse_ensemble_payload(
            _ensemble_payload(240, members=9),
            source=source_for_id("ecmwf_ens"),
            point_label="Пункт проверки ансамблевой метеограммы",
            requested_lat=55.75,
            requested_lon=37.62,
        )
        figure, axes, _tracked = build_meteogram_figure(series)
        try:
            probability_axis = axes[3]._meteogram_probability_axis
            self.assertIsNotNone(probability_axis)
            self.assertEqual(len(probability_axis.lines), 3)
        finally:
            plt.close(figure)
        result = self._render(series)
        self.assertEqual(result["photo_safe"], 1)

    def test_overlap_resolver_hides_lower_priority(self):
        figure = plt.figure(figsize=(4, 2), dpi=100)
        high = figure.text(0.2, 0.5, "важная подпись", fontsize=12)
        low = figure.text(0.2, 0.5, "вторичная подпись", fontsize=12)
        _resolve_overlaps(figure, [(high, 100), (low, 10)])
        self.assertTrue(high.get_visible())
        self.assertFalse(low.get_visible())
        plt.close(figure)


if __name__ == "__main__":
    unittest.main()
