from __future__ import annotations

import inspect
import unittest

import numpy as np

from composite_map_meteorology import _forecast_interval_hours, _storm_grid, build_composite_map


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

    def test_lightning_mask_requires_score_three_and_precipitation(self) -> None:
        source = inspect.getsource(build_composite_map)
        self.assertIn("convective_score >= 3.0", source)
        self.assertIn("precip_for_storm > 0.2", source)
        self.assertNotIn("convective_score >= 2.0", source)

    def test_precipitation_rate_fallback_is_integrated_to_amount(self) -> None:
        source = inspect.getsource(build_composite_map)
        self.assertIn("prate_item[0] * 3600.0 * expected_interval", source)
        self.assertIn('precip_source = "PRATE integrated"', source)


if __name__ == "__main__":
    unittest.main()
