from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from aero_plot import _augment_profile, _diagnose_layers, _interpolate_isotherm_height, _risk_level


def sample_profile() -> pd.DataFrame:
    pressure = np.array([1000, 900, 800, 700, 600, 500], dtype=float)
    height = np.array([100, 1000, 2000, 3000, 4200, 5600], dtype=float)
    temp_c = np.array([15, 8, 1, -6, -15, -25], dtype=float)
    dewpoint_c = np.array([10, 6, -1, -7, -16, -30], dtype=float)
    temp_k = temp_c + 273.15
    return pd.DataFrame(
        {
            "pressure_hpa": pressure,
            "geopotential_height_m": height,
            "geopotential_height_km": height / 1000.0,
            "temperature_c": temp_c,
            "temperature_k": temp_k,
            "dewpoint_c": dewpoint_c,
            "relative_humidity_pct": [72, 80, 88, 92, 91, 60],
            "u_wind_ms": [0, 4, 9, 18, 28, 40],
            "v_wind_ms": [0, 1, 4, 9, 16, 25],
            "wind_speed_ms": np.hypot([0, 4, 9, 18, 28, 40], [0, 1, 4, 9, 16, 25]),
            "theta_k": temp_k * np.power(1000.0 / pressure, 0.286),
        }
    )


class AeroPlotDiagnosticsTest(unittest.TestCase):
    def test_isotherm_heights_interpolate_minus10_and_minus20(self) -> None:
        df = sample_profile()
        minus10 = _interpolate_isotherm_height(df, -10.0)
        minus20 = _interpolate_isotherm_height(df, -20.0)
        self.assertIsNotNone(minus10)
        self.assertIsNotNone(minus20)
        self.assertAlmostEqual(float(minus10), 3533.3, delta=5.0)
        self.assertAlmostEqual(float(minus20), 4900.0, delta=5.0)

    def test_augment_profile_adds_shear_richardson_and_thetae(self) -> None:
        df = _augment_profile(sample_profile())
        self.assertIn("vertical_shear_ms_per_km", df)
        self.assertIn("gradient_richardson", df)
        self.assertIn("thetae_lapse_k_per_km", df)
        self.assertTrue(np.isfinite(df["vertical_shear_ms_per_km"]).any())
        self.assertTrue(np.isfinite(df["thetae_k"]).any())

    def test_diagnose_layers_marks_cloud_icing_turbulence_and_convective_layer(self) -> None:
        df = _augment_profile(sample_profile())
        df.loc[:, "thetae_lapse_k_per_km"] = [1.0, -4.0, -5.0, 1.0, 1.0, 1.0]
        layers = _diagnose_layers(df)
        kinds = {layer["kind"] for layer in layers}
        self.assertIn("cloud", kinds)
        self.assertIn("icing", kinds)
        self.assertIn("turb", kinds)
        self.assertIn("conv", kinds)

    def test_index_risk_levels_highlight_critical_values(self) -> None:
        self.assertEqual(_risk_level("cape", 0), 0)
        self.assertGreaterEqual(_risk_level("cape", 1600), 4)
        self.assertGreaterEqual(_risk_level("cin", -120), 3)
        self.assertGreaterEqual(_risk_level("tt", 52), 3)
        self.assertGreaterEqual(_risk_level("k", 32), 3)
        self.assertGreaterEqual(_risk_level("shear", 12), 4)
        self.assertGreaterEqual(_risk_level("ri", 0.2), 4)


if __name__ == "__main__":
    unittest.main()
