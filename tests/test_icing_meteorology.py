from __future__ import annotations

import unittest

import numpy as np

from icing_meteorology import (
    G0, compute_icing_fields, layer_products, select_icing_field,
)


def column(temperature, *, margin=None, water=None, heights=None, pressure=None):
    """Idealised columns test numerical contracts, not meteorological accuracy."""
    t = np.array(temperature, dtype=float)
    n = len(t)
    p = np.asarray(pressure if pressure is not None else np.linspace(1000, 700, n))
    z = np.asarray(heights if heights is not None else np.arange(n) * 100.0)
    deficit = (np.asarray(margin) - t) / 8 if margin is not None else np.ones(n) * 0.2
    td = t - deficit
    e = 610.94 * np.exp(17.625 * td / (243.04 + td))
    q = 0.622 * e / (p * 100 - 0.378 * e)
    rho = p * 100 / (287.05 * (t + 273.15) * (1 + (1 / 0.622 - 1) * q))
    lwc = np.asarray(water if water is not None else np.ones(n) * 0.1)
    return dict(
        pressure_hpa=p,
        temperature_k=(t + 273.15)[:, None, None],
        specific_humidity=q[:, None, None],
        height_gpm=z[:, None, None],
        cloud_water_kgkg=(lwc / (1000 * rho))[:, None, None],
        surface_pressure_pa=np.array([[101000.0]]),
        surface_height_m=np.array([[-10.0]]),
    )


class IcingMeteorologyTests(unittest.TestCase):
    def test_disjoint_layers_exclude_gap(self):
        z = np.arange(7.0)[:, None, None] * 100
        margin = np.array([1, -1, -1, 1, 1, -1, 1])[:, None, None]
        result = layer_products(z, np.ones_like(z, dtype=bool), [margin])
        self.assertAlmostEqual(float(result["sum_gpm"][0, 0]), 300)
        self.assertAlmostEqual(float(result["max_layer_gpm"][0, 0]), 200)
        self.assertAlmostEqual(float(result["envelope_gpm"][0, 0]), 500)
        self.assertEqual(result["n_layers"][0, 0], 2)
        self.assertEqual(result["base_first_gpm"][0, 0], 50)
        self.assertEqual(result["top_first_gpm"][0, 0], 250)

    def test_interval_can_exist_between_inactive_end_nodes(self):
        z = np.array([0.0, 1000.0])[:, None, None]
        c1 = np.array([-1.0, 1.0])[:, None, None]
        c2 = np.array([1.0, -3.0])[:, None, None]
        result = layer_products(z, np.ones_like(z, dtype=bool), [c1, c2])
        self.assertAlmostEqual(result["sum_gpm"][0, 0], 250)
        self.assertAlmostEqual(result["base_first_gpm"][0, 0], 250)
        self.assertAlmostEqual(result["top_first_gpm"][0, 0], 500)

    def test_zero_temperature_plateau_is_excluded(self):
        result = compute_icing_fields(**column([0, 0, 0]))
        self.assertEqual(result["godske20_sum_gpm"][0, 0], 0)
        self.assertEqual(result["slw001_sum_gpm"][0, 0], 0)
        self.assertEqual(result["slwp_gm2"][0, 0], 0)
        self.assertTrue(np.isnan(result["max_slwc_gm3"][0, 0]))

    def test_slwp_integrates_only_cold_half(self):
        args = column([-10, 10], pressure=[1000, 900], heights=[0, 1000])
        args["cloud_water_kgkg"][:] = 1e-4
        result = compute_icing_fields(**args)
        self.assertAlmostEqual(result["slwp_gm2"][0, 0], 1000 / G0 * 1e-4 * 5000)

    def test_missing_water_does_not_remove_godske(self):
        args = column([-10, -10, -10])
        args["cloud_water_kgkg"][1, 0, 0] = np.nan
        result = compute_icing_fields(**args)
        self.assertAlmostEqual(result["godske20_sum_gpm"][0, 0], 200)
        self.assertTrue(np.isnan(result["slwp_gm2"][0, 0]))
        self.assertTrue(np.isnan(result["agreement"][0, 0]))
        self.assertTrue(result["SLWP_interior_missing"][0, 0])

    def test_negative_water_is_unknown_not_zero(self):
        args = column([-10, -10])
        args["cloud_water_kgkg"][:] = -1e-4
        result = compute_icing_fields(**args)
        self.assertAlmostEqual(result["godske20_sum_gpm"][0, 0], 100)
        self.assertTrue(np.isnan(result["max_slwc_gm3"][0, 0]))
        self.assertTrue(np.isnan(result["slwp_gm2"][0, 0]))

    def test_missing_interior_level_is_not_bridged(self):
        args = column([-10] * 5)
        args["temperature_k"][2] = np.nan
        result = compute_icing_fields(**args)
        self.assertEqual(result["godske20_sum_gpm"][0, 0], 200)
        self.assertEqual(result["godske20_n_layers"][0, 0], 2)
        self.assertTrue(result["godske20_interior_missing"][0, 0])
        self.assertTrue(np.isnan(select_icing_field(result, "godske_top")[0, 0]))

    def test_below_ground_levels_are_removed_without_surface_extension(self):
        args = column([-10] * 3, pressure=[1000, 900, 800], heights=[0, 1000, 2000])
        args["surface_pressure_pa"][:] = 95000
        args["surface_height_m"][:] = 300
        result = compute_icing_fields(**args)
        self.assertAlmostEqual(result["godske20_sum_gpm"][0, 0], 1000)
        self.assertTrue(result["godske20_lower_open"][0, 0])
        self.assertTrue(np.isnan(select_icing_field(result, "godske_base")[0, 0]))
        self.assertAlmostEqual(result["unresolved_bottom_depth_m"][0, 0], 700)

    def test_open_upper_layer_does_not_hide_closed_first_top(self):
        result = compute_icing_fields(**column([-10] * 6, margin=[1, -1, 1, 1, -1, -1]))
        self.assertTrue(result["godske20_upper_open"][0, 0])
        self.assertFalse(result["godske20_top_first_open"][0, 0])
        self.assertAlmostEqual(select_icing_field(result, "godske_top")[0, 0], 150, places=8)

    def test_one_valid_level_does_not_create_layer_or_column_integral(self):
        result = compute_icing_fields(**column([-10]))
        self.assertTrue(np.isnan(result["godske20_sum_gpm"][0, 0]))
        self.assertTrue(np.isnan(result["godske20_n_layers"][0, 0]))
        self.assertTrue(np.isnan(result["slwp_gm2"][0, 0]))
        self.assertAlmostEqual(result["max_slwc_gm3"][0, 0], 0.1)

    def test_zero_water_has_no_peak_height(self):
        result = compute_icing_fields(**column([-10, -10], water=[0, 0]))
        self.assertEqual(result["max_slwc_gm3"][0, 0], 0)
        self.assertTrue(np.isnan(result["max_slwc_height_gpm"][0, 0]))
        self.assertTrue(np.isnan(result["max_slwc_pressure_hpa"][0, 0]))

    def test_dry_humidity_is_allowed_for_density_not_dewpoint(self):
        args = column([-10, -10])
        args["specific_humidity"][:] = 0
        result = compute_icing_fields(**args)
        self.assertTrue(np.isnan(result["godske20_sum_gpm"][0, 0]))
        self.assertGreater(result["slwp_gm2"][0, 0], 0)
        self.assertGreater(result["max_slwc_gm3"][0, 0], 0)

    def test_agreement_distinguishes_different_layers(self):
        args = column([-10] * 6, margin=[-1, -1, 1, 1, 1, 1], water=[0, 0, 0, 0, .1, .1])
        result = compute_icing_fields(**args)
        self.assertEqual(result["agreement"][0, 0], 4)
        self.assertEqual(result["agreement_overlap_gpm"][0, 0], 0)
        args = column([-10] * 6, margin=[-1, -1, 1, 1, 1, 1], water=[.1, .1, 0, 0, 0, 0])
        result = compute_icing_fields(**args)
        self.assertEqual(result["agreement"][0, 0], 3)
        self.assertGreater(result["agreement_overlap_gpm"][0, 0], 0)

    def test_level_water_is_zero_when_warm_and_nan_when_underground(self):
        args = column([10, -10], pressure=[1000, 850])
        result = compute_icing_fields(**args)
        self.assertEqual(select_icing_field(result, "slw_level", 1000)[0, 0], 0)
        args["surface_pressure_pa"][:] = 90000
        result = compute_icing_fields(**args)
        self.assertTrue(np.isnan(select_icing_field(result, "slw_level", 1000)[0, 0]))
        with self.assertRaises(ValueError):
            select_icing_field(result, "slw_level", 800)

    def test_bad_dimensions_pressure_and_height_are_rejected(self):
        for change in (
            {"pressure_hpa": np.array([1000, 1000])},
            {"pressure_hpa": np.array([850, 1000])},
            {"height_gpm": np.array([1000, 900])[:, None, None]},
            {"surface_height_m": np.zeros((2, 2))},
        ):
            args = column([-10, -10])
            args.update(change)
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                compute_icing_fields(**args)


if __name__ == "__main__":
    unittest.main()
