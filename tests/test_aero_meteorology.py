from __future__ import annotations

import inspect
import math
import unittest

import numpy as np
import pandas as pd

from aero_meteorology import _height_at_pressure_log, diagnose_layers, metpy_diagnostics


class AeroMeteorologyTests(unittest.TestCase):
    def test_pwat_uses_metpy_pressure_dewpoint_signature(self) -> None:
        source = inspect.getsource(metpy_diagnostics)
        self.assertIn("precipitable_water(pressure, dewpoint)", source)
        self.assertNotIn("precipitable_water(pressure, temperature, dewpoint)", source)

    def test_pressure_to_height_interpolation_is_logarithmic(self) -> None:
        frame = pd.DataFrame(
            {
                "pressure_hpa": [1000.0, 500.0],
                "geopotential_height_m": [100.0, 5600.0],
            }
        )
        middle_pressure = math.sqrt(1000.0 * 500.0)
        height = _height_at_pressure_log(frame, middle_pressure)
        self.assertAlmostEqual(height, 2850.0, places=5)

    def test_layers_are_explicitly_named_as_model_proxies(self) -> None:
        frame = pd.DataFrame(
            {
                "pressure_hpa": [1000.0, 900.0, 800.0],
                "geopotential_height_m": [100.0, 1000.0, 2100.0],
                "geopotential_height_km": [0.1, 1.0, 2.1],
                "temperature_c": [-2.0, -8.0, -15.0],
                "dewpoint_c": [-2.5, -8.5, -15.5],
                "relative_humidity_pct": [95.0, 95.0, 95.0],
                "cloud_proxy": [True, True, True],
                "icing_proxy_score": [1, 2, 2],
                "turbulence_proxy_score": [0, 2, 2],
                "thetae_lapse_k_per_km": [0.0, -4.0, -4.0],
                "rain_mixing_ratio_kgkg": [0.0, 1e-6, 1e-6],
                "snow_mixing_ratio_kgkg": [0.0, 0.0, 1e-6],
                "graupel_mixing_ratio_kgkg": [0.0, 0.0, 0.0],
            }
        )
        layers = diagnose_layers(frame)
        labels = {str(layer["label"]) for layer in layers}
        self.assertIn("Прокси обледенения", labels)
        self.assertIn("Прокси болтанки", labels)
        self.assertIn("Гидрометеоры осадков", labels)
        self.assertTrue(all(int(layer["severity"]) >= 1 for layer in layers))


if __name__ == "__main__":
    unittest.main()
