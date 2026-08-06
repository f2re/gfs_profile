from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from meteogram_core import (
    MeteogramError,
    parse_deterministic_payload,
    parse_ensemble_payload,
    source_for_id,
    validate_days,
)
from meteogram_plot import audit_meteogram_layout, write_meteogram_png
from meteogram_request import parse_meteogram_request


def _times(hours: int = 96) -> list[str]:
    start = datetime(2026, 8, 6, tzinfo=UTC)
    return [(start + timedelta(hours=index * 3)).strftime("%Y-%m-%dT%H:%M") for index in range(hours // 3 + 1)]


def _deterministic_payload(hours: int = 120) -> dict:
    times = _times(hours)
    count = len(times)
    phase = np.linspace(0, 4 * np.pi, count)
    return {
        "latitude": 55.75,
        "longitude": 37.625,
        "timezone": "Europe/Moscow",
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
    return {"latitude": 55.75, "longitude": 37.625, "timezone": "Europe/Moscow", "hourly": hourly}


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


if __name__ == "__main__":
    unittest.main()
