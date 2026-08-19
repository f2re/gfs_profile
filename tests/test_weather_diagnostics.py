from __future__ import annotations

import unittest

from weather_diagnostics import (
    DASH,
    instant_weather_code,
    precipitation_code,
    thunder_score,
    visibility_km,
    weather_code,
)


class WeatherDiagnosticsTests(unittest.TestCase):
    def test_gfs_visibility_metres_are_always_converted_to_kilometres(self) -> None:
        expected = {
            50.0: 0.05,
            100.0: 0.1,
            200.0: 0.2,
            1000.0: 1.0,
            8000.0: 8.0,
            10000.0: 10.0,
        }
        for metres, kilometres in expected.items():
            self.assertAlmostEqual(visibility_km(metres), kilometres, places=6)
        self.assertIsNone(visibility_km(None))

    def test_cloud_and_weak_cin_do_not_create_thunder(self) -> None:
        score = thunder_score(
            cape=0.0,
            cin=-50.0,
            conv_precip_mm=0.0,
            conv_cloud_pct=100.0,
            precip_rate_mmh=0.0,
        )
        self.assertEqual(score, 0)
        self.assertEqual(weather_code(0.0, DASH, score, 20.0), DASH)

    def test_total_precipitation_must_not_replace_convective_rate(self) -> None:
        score = thunder_score(
            cape=1200.0,
            cin=-40.0,
            conv_precip_mm=0.0,
            conv_cloud_pct=0.0,
            precip_rate_mmh=0.0,
        )
        self.assertEqual(score, 1)

    def test_convective_potential_is_not_labelled_tsra(self) -> None:
        score = thunder_score(
            cape=700.0,
            cin=-80.0,
            conv_precip_mm=0.2,
            conv_cloud_pct=60.0,
            precip_rate_mmh=0.8,
        )
        self.assertEqual(score, 2)
        self.assertEqual(weather_code(1.0, "R", score, 20.0), "RA")

    def test_strong_convection_with_precipitation_is_tsra(self) -> None:
        score = thunder_score(
            cape=1600.0,
            cin=-40.0,
            conv_precip_mm=1.2,
            conv_cloud_pct=70.0,
            precip_rate_mmh=4.0,
        )
        self.assertEqual(score, 3)
        self.assertEqual(weather_code(2.0, "R", score, 20.0), "TSRA")

    def test_convective_precip_without_cape_is_only_weak_signal(self) -> None:
        score = thunder_score(
            cape=None,
            cin=None,
            conv_precip_mm=1.0,
            conv_cloud_pct=None,
            precip_rate_mmh=2.0,
        )
        self.assertEqual(score, 1)
        self.assertEqual(weather_code(1.0, "R", score, 20.0), "RA")

    def test_precipitation_codes(self) -> None:
        self.assertEqual(precipitation_code(True, False, False, False), "R")
        self.assertEqual(precipitation_code(False, True, False, False), "S")
        self.assertEqual(precipitation_code(True, True, False, False), "R/S")
        self.assertEqual(precipitation_code(False, False, False, True), "IP")
        self.assertEqual(weather_code(0.0, DASH, 0, 0.5), "FG")
        self.assertEqual(weather_code(1.0, "FZ", 0, 8.0), "FZRA")
        self.assertEqual(weather_code(1.0, "S", 0, 8.0), "SN")
        self.assertEqual(weather_code(1.0, "R", 0, 8.0), "RA")

    def test_instant_rain_requires_rate_and_explicit_rain_category(self) -> None:
        self.assertEqual(instant_weather_code(None, "R", 0, 10.0), DASH)
        self.assertEqual(instant_weather_code(0.09, "R", 0, 10.0), DASH)
        self.assertEqual(instant_weather_code(0.10, "R", 0, 10.0), "RA")
        self.assertEqual(instant_weather_code(0.40, DASH, 0, 10.0), "UP")

    def test_instant_phase_identification_is_not_forced_to_rain(self) -> None:
        self.assertEqual(instant_weather_code(0.5, "S", 0, 10.0), "SN")
        self.assertEqual(instant_weather_code(0.5, "FZ", 0, 10.0), "FZRA")
        self.assertEqual(instant_weather_code(0.5, "IP", 0, 10.0), "IP")
        self.assertEqual(instant_weather_code(0.5, "R/S", 0, 10.0), "RASN")

    def test_instant_thunder_requires_active_precipitation(self) -> None:
        self.assertEqual(instant_weather_code(0.19, "R", 3, 10.0), "RA")
        self.assertEqual(instant_weather_code(0.20, "R", 3, 10.0), "TSRA")
        self.assertEqual(instant_weather_code(0.50, "S", 3, 10.0), "TSSN")
        self.assertEqual(instant_weather_code(0.50, DASH, 3, 10.0), "TS")

    def test_instant_fog_is_shown_only_without_active_precipitation(self) -> None:
        self.assertEqual(instant_weather_code(0.0, DASH, 0, 0.5), "FG")
        self.assertEqual(instant_weather_code(0.4, "R", 0, 0.5), "RA")


if __name__ == "__main__":
    unittest.main()
