from __future__ import annotations

import inspect
import unittest

import numpy as np

from composite_map_meteorology import (
    MAP_ACTIVE_PRECIP_RATE_MMH,
    _forecast_interval_hours,
    _phenomenon_grid,
    _storm_grid,
    build_composite_map,
)
from weather_diagnostics import DASH


class CompositeMapMeteorologyTests(unittest.TestCase):
    def test_native_gfs_interval_is_one_hour_then_three_hours(self) -> None:
        self.assertEqual(_forecast_interval_hours(0), 1.0)
        self.assertEqual(_forecast_interval_hours(120), 1.0)
        self.assertEqual(_forecast_interval_hours(123), 3.0)
        self.assertEqual(_forecast_interval_hours(384), 3.0)

    def test_storm_grid_uses_only_convective_evidence(self) -> None:
        cape = np.asarray([[1200.0, 1200.0]])
        cin = np.asarray([[-40.0, -40.0]])
        conv_precip = np.asarray([[0.0, 1.0]])
        conv_cloud = np.asarray([[0.0, 60.0]])
        conv_rate = np.asarray([[0.0, 4.0]])
        scores = _storm_grid(cape, cin, conv_precip, conv_cloud, conv_rate)
        self.assertEqual(float(scores[0, 0]), 1.0)
        self.assertEqual(float(scores[0, 1]), 3.0)

    def test_storm_grid_normalizes_acpcp_interval(self) -> None:
        cape = np.asarray([[700.0]])
        cin = np.asarray([[-80.0]])
        conv_cloud = np.asarray([[40.0]])
        conv_rate = np.asarray([[0.0]])
        one_hour = _storm_grid(cape, cin, np.asarray([[0.3]]), conv_cloud, conv_rate, 1.0)
        three_hour = _storm_grid(cape, cin, np.asarray([[0.9]]), conv_cloud, conv_rate, 3.0)
        np.testing.assert_allclose(one_hour, three_hour)
        self.assertEqual(float(one_hour[0, 0]), 2.0)

    def test_phenomena_are_classified_per_exact_grid_cell(self) -> None:
        rate = np.asarray([[0.09, 0.3, 0.6], [0.4, 0.5, 0.7]])
        rain = np.asarray([[True, True, False], [False, True, False]])
        snow = np.asarray([[False, False, True], [False, True, False]])
        freezing = np.asarray([[False, False, False], [True, False, False]])
        ice = np.asarray([[False, False, False], [False, False, True]])
        storm = np.asarray([[0.0, 0.0, 0.0], [0.0, 0.0, 3.0]])
        vis = np.full_like(rate, 10.0)

        codes = _phenomenon_grid(rate, rain, snow, freezing, ice, storm, vis)
        self.assertEqual(codes.shape, rate.shape)
        self.assertEqual(codes[0, 0], DASH)
        self.assertEqual(codes[0, 1], "RA")
        self.assertEqual(codes[0, 2], "SN")
        self.assertEqual(codes[1, 0], "FZRA")
        self.assertEqual(codes[1, 1], "RASN")
        self.assertEqual(codes[1, 2], "TS")

    def test_positive_rate_without_rain_flag_never_becomes_rain(self) -> None:
        rate = np.asarray([[0.8]])
        false = np.asarray([[False]])
        codes = _phenomenon_grid(rate, false, false, false, false, np.asarray([[0.0]]), np.asarray([[10.0]]))
        self.assertEqual(codes[0, 0], "UP")

    def test_rain_symbol_threshold_matches_documented_rate_threshold(self) -> None:
        false = np.asarray([[False, False]])
        rain = np.asarray([[True, True]])
        rate = np.asarray([[MAP_ACTIVE_PRECIP_RATE_MMH - 0.001, MAP_ACTIVE_PRECIP_RATE_MMH]])
        codes = _phenomenon_grid(rate, rain, false, false, false, np.zeros_like(rate), np.full_like(rate, 10.0))
        self.assertEqual(codes[0, 0], DASH)
        self.assertEqual(codes[0, 1], "RA")

    def test_builder_uses_prate_for_current_phenomena_not_apcp_amount(self) -> None:
        source = inspect.getsource(build_composite_map)
        self.assertIn("precip_rate_mmh", source)
        self.assertIn("phenomenon_code", source)
        self.assertIn("_phenomenon_grid", source)
        self.assertNotIn("weather_code(precip", source)

    def test_precipitation_rate_fallback_is_integrated_to_amount(self) -> None:
        source = inspect.getsource(build_composite_map)
        self.assertIn("prate_item[0] * 3600.0 * expected_interval", source)
        self.assertIn('precip_source = "PRATE integrated"', source)


if __name__ == "__main__":
    unittest.main()
