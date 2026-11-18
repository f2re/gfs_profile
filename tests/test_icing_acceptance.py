"""Independent physical invariants for the icing integration.

Idealized columns here are numerical unit checks, not meteorological examples
or observations. The archived real-GFS comparison is reported separately.
"""
from __future__ import annotations

import unittest

import numpy as np

from icing_meteorology import compute_icing_fields


class IcingPhysicalAcceptanceTests(unittest.TestCase):
    def _column(self, *, water_scale=1.0, height_scale=1.0, missing_water=False):
        pressure = np.array([1000.0, 850.0, 700.0])
        temperature = np.full((3, 1, 1), 263.15)
        # Td=-11 C, T=-10 C gives G=-2 C throughout the column.
        e = 610.94 * np.exp(17.625 * -11.0 / (243.04 - 11.0))
        specific = (0.622 * e / (pressure * 100 - 0.378 * e))[:, None, None]
        height = (100.0 + height_scale * np.array([0.0, 1600.0, 3100.0]))[:, None, None]
        liquid = np.full((3, 1, 1), 1e-4 * water_scale)
        if missing_water:
            liquid[:] = np.nan
        return compute_icing_fields(
            pressure, temperature, specific, height, liquid,
            np.array([[101300.0]]), np.array([[0.0]]),
        )

    def test_water_amount_does_not_relabel_godske_geometry(self):
        base = self._column()
        wetter = self._column(water_scale=3.0)
        self.assertAlmostEqual(float(base["godske20_sum_gpm"][0, 0]), 3100.0)
        np.testing.assert_equal(base["godske20_sum_gpm"], wetter["godske20_sum_gpm"])
        np.testing.assert_equal(base["godske20_n_layers"], wetter["godske20_n_layers"])
        np.testing.assert_allclose(wetter["slwp_gm2"], 3.0 * base["slwp_gm2"], rtol=1e-13)
        np.testing.assert_allclose(wetter["max_slwc_gm3"], 3.0 * base["max_slwc_gm3"], rtol=1e-13)

    def test_hydrostatic_water_path_is_mass_per_area_not_geometric_thickness(self):
        base = self._column()
        stretched = self._column(height_scale=2.0)
        # Hydrostatic mass integral for a constant q_l between 1000 and 700 hPa.
        expected = 1000.0 * 1e-4 * (100000.0 - 70000.0) / 9.80665
        self.assertAlmostEqual(float(base["slwp_gm2"][0, 0]), expected, places=10)
        np.testing.assert_allclose(stretched["godske20_sum_gpm"], 2 * base["godske20_sum_gpm"])
        np.testing.assert_allclose(stretched["slwp_gm2"], base["slwp_gm2"], rtol=1e-13)

    def test_missing_liquid_water_preserves_thermodynamic_diagnosis(self):
        base = self._column()
        unknown_water = self._column(missing_water=True)
        np.testing.assert_equal(base["godske20_sum_gpm"], unknown_water["godske20_sum_gpm"])
        self.assertTrue(np.isnan(unknown_water["slwp_gm2"]).all())
        self.assertTrue(np.isnan(unknown_water["max_slwc_gm3"]).all())
        self.assertTrue(np.isnan(unknown_water["agreement"]).all())
        self.assertFalse(unknown_water["SLWP_has_coverage"].any())


if __name__ == "__main__":
    unittest.main()
