from __future__ import annotations

import unittest
import warnings
from unittest.mock import patch

import numpy as np

from meteogram_fetch import fetch_meteogram
from meteogram_models import MeteogramError, available_periods, source_for_id
from meteogram_parse import (
    _validate_payload_units,
    parse_ensemble_payload,
)


TIMES = ["2026-08-11T06:00", "2026-08-11T07:00", "2026-08-11T08:00"]


def _ensemble_payload() -> dict:
    return {
        "latitude": 59.9375,
        "longitude": 30.3125,
        "timezone": "Europe/Moscow",
        "hourly_units": {
            "temperature_2m": "°C",
            "precipitation": "mm",
            "wind_speed_10m": "m/s",
            "wind_gusts_10m": "m/s",
        },
        "hourly": {
            "time": list(TIMES),
            # Open-Meteo contract: member 0 may be unsuffixed.
            "temperature_2m": [15.0, 16.0, 17.0],
            "temperature_2m_member01": [16.0, 17.0, 18.0],
            "precipitation": [0.0, 0.2, 0.0],
            "precipitation_member01": [0.0, 0.4, 0.0],
            "wind_speed_10m": [4.0, 5.0, 6.0],
            "wind_speed_10m_member01": [5.0, 6.0, 7.0],
            "wind_gusts_10m": [8.0, 9.0, 10.0],
            "wind_gusts_10m_member01": [9.0, 10.0, 11.0],
        },
    }


class MeteogramProviderContractTests(unittest.TestCase):
    def test_unsuffixed_member_zero_is_retained(self) -> None:
        payload = _ensemble_payload()
        _validate_payload_units(payload, source_for_id("icon_eps"))
        series = parse_ensemble_payload(
            payload,
            source=source_for_id("icon_eps"),
            point_label="Санкт-Петербург",
            requested_lat=59.939,
            requested_lon=30.316,
        )
        self.assertEqual(series.member_count, 2)
        np.testing.assert_allclose(series.values("temperature_2m"), [15.5, 16.5, 17.5])
        np.testing.assert_allclose(series.values("ensemble_member_count"), [2.0, 2.0, 2.0])

    def test_undefined_gust_unit_drops_only_gusts(self) -> None:
        payload = _ensemble_payload()
        payload["hourly_units"]["wind_gusts_10m"] = "undefined"
        source = source_for_id("icon_eps")
        _validate_payload_units(payload, source)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            series = parse_ensemble_payload(
                payload,
                source=source,
                point_label="Санкт-Петербург",
                requested_lat=59.939,
                requested_lon=30.316,
            )
        self.assertTrue(np.isnan(series.values("wind_gusts_10m")).all())
        self.assertTrue(np.isfinite(series.values("wind_speed_10m")).all())
        self.assertIn("wind_gusts_10m недоступно", " ".join(series.warnings))

    def test_wrong_unit_with_data_remains_fatal(self) -> None:
        payload = _ensemble_payload()
        payload["hourly_units"]["wind_speed_10m"] = "km/h"
        with self.assertRaises(MeteogramError):
            _validate_payload_units(payload, source_for_id("icon_eps"))

    def test_fetch_uses_exact_hours_and_raw_model_grid(self) -> None:
        payload = {
            "latitude": 55.75,
            "longitude": 37.625,
            "timezone": "Europe/Moscow",
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {
                "time": list(TIMES),
                "temperature_2m": [10.0, 11.0, 12.0],
            },
        }
        with (
            patch("meteogram_fetch._read_cache", return_value=None),
            patch("meteogram_fetch._request_json", return_value=payload) as request_json,
            patch("meteogram_fetch._write_cache"),
        ):
            series = fetch_meteogram("gfs", "Москва", 55.75, 37.62, 1)
        params = request_json.call_args.args[1]
        self.assertEqual(params["forecast_hours"], 24)
        self.assertNotIn("forecast_days", params)
        self.assertEqual(params["elevation"], "nan")
        self.assertTrue(np.isfinite(series.values("temperature_2m")).all())

    def test_icon_eps_offers_only_complete_days(self) -> None:
        source = source_for_id("icon_eps")
        self.assertEqual(source.horizon_days, 7)
        self.assertEqual(available_periods(source), (3, 5, 7))


if __name__ == "__main__":
    unittest.main()
