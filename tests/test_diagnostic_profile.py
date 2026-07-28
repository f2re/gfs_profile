from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from diagnostic_profile import (
    add_profile_diagnostics,
    air_density_kg_m3,
    icing_proxy_score,
    pressure_level_tokens,
    turbulence_proxy_score,
)


class DiagnosticProfileTests(unittest.TestCase):
    def test_pressure_tokens_match_nomads_filter_names(self) -> None:
        self.assertEqual(
            pressure_level_tokens((1000, 850, 500)),
            ("lev_1000_mb", "lev_850_mb", "lev_500_mb"),
        )

    def test_moist_air_density_is_physically_plausible(self) -> None:
        density = air_density_kg_m3(
            np.asarray([1000.0]),
            np.asarray([293.15]),
            np.asarray([50.0]),
        )
        self.assertGreater(float(density[0]), 1.15)
        self.assertLess(float(density[0]), 1.22)
        scalar = air_density_kg_m3(1000.0, 293.15, 50.0)
        self.assertGreater(float(scalar), 1.15)
        self.assertLess(float(scalar), 1.22)

    def test_icing_proxy_uses_slwc_and_caps_rh_fallback(self) -> None:
        self.assertEqual(icing_proxy_score(-10.0, 0.25, 95.0, microphysics_available=True), 3)
        self.assertEqual(icing_proxy_score(-10.0, 0.07, 95.0, microphysics_available=True), 2)
        self.assertEqual(icing_proxy_score(-10.0, 0.01, 95.0, microphysics_available=True), 1)
        self.assertEqual(icing_proxy_score(-10.0, 0.0, 100.0, microphysics_available=True), 0)
        self.assertEqual(icing_proxy_score(-10.0, None, 98.0, microphysics_available=False), 1)
        self.assertEqual(icing_proxy_score(-10.0, None, 70.0, microphysics_available=False), 0)

    def test_turbulence_proxy_requires_shear_and_stability_for_top_score(self) -> None:
        self.assertEqual(turbulence_proxy_score(16.0, 0.20), 3)
        self.assertEqual(turbulence_proxy_score(16.0, 1.50), 2)
        self.assertEqual(turbulence_proxy_score(11.0, 0.40), 2)
        self.assertEqual(turbulence_proxy_score(6.5, 2.00), 1)
        self.assertEqual(turbulence_proxy_score(3.0, 0.20), 0)

    @staticmethod
    def _base_frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "pressure_hpa": [1000.0, 900.0, 800.0, 700.0],
                "temperature_k": [273.15, 268.15, 263.15, 258.15],
                "temperature_c": [0.0, -5.0, -10.0, -15.0],
                "dewpoint_c": [-0.5, -5.5, -10.5, -15.5],
                "relative_humidity_pct": [96.0, 96.0, 96.0, 96.0],
                "u_wind_ms": [0.0, 8.0, 20.0, 35.0],
                "v_wind_ms": [0.0, 1.0, 4.0, 8.0],
                "geopotential_height_m": [100.0, 1000.0, 2100.0, 3300.0],
            }
        )

    def test_profile_diagnostics_convert_mixing_ratio_to_content(self) -> None:
        frame = self._base_frame()
        frame["cloud_liquid_mixing_ratio_kgkg"] = [0.00020, 0.00010, 0.00005, 0.0]
        frame["cloud_ice_mixing_ratio_kgkg"] = [0.0, 0.0, 0.00002, 0.00005]
        frame["rain_mixing_ratio_kgkg"] = [0.0, 0.0, 0.0, 0.0]
        frame["snow_mixing_ratio_kgkg"] = [0.0, 0.0, 0.0, 0.0]
        frame["graupel_mixing_ratio_kgkg"] = [0.0, 0.0, 0.0, 0.0]
        out = add_profile_diagnostics(frame)
        for column in (
            "air_density_kg_m3",
            "supercooled_liquid_water_content_gm3",
            "total_condensate_gm3",
            "cloud_proxy",
            "icing_proxy_score",
            "vertical_shear_ms_per_km",
            "gradient_richardson",
            "turbulence_proxy_score",
        ):
            self.assertIn(column, out.columns)
        self.assertGreater(float(out["supercooled_liquid_water_content_gm3"].max()), 0.05)
        self.assertTrue(bool(out["cloud_proxy"].any()))
        self.assertGreaterEqual(int(out["icing_proxy_score"].max()), 2)

    def test_ice_only_microphysics_does_not_suppress_liquid_water_fallback(self) -> None:
        frame = self._base_frame()
        frame["cloud_liquid_mixing_ratio_kgkg"] = np.nan
        frame["rain_mixing_ratio_kgkg"] = np.nan
        frame["cloud_ice_mixing_ratio_kgkg"] = [0.00002] * 4
        frame["snow_mixing_ratio_kgkg"] = [0.0] * 4
        frame["graupel_mixing_ratio_kgkg"] = [0.0] * 4
        out = add_profile_diagnostics(frame)
        self.assertTrue(bool(out["microphysics_available"].all()))
        self.assertFalse(bool(out["liquid_microphysics_available"].any()))
        self.assertEqual(int(out["icing_proxy_score"].max()), 1)
        self.assertTrue(all(value.startswith("T/RH fallback") for value in out["icing_proxy_source"]))


if __name__ == "__main__":
    unittest.main()
