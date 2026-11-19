from __future__ import annotations

import unittest

import numpy as np

from icing_meteorology import (
    G0, compute_icing_fields, layer_products, select_icing_field, sfip_index, sfip_memberships,
)


def column(temperature, *, margin=None, water=None, heights=None, pressure=None, cloud=None, omega=None):
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
        cloud_fraction_pct=np.asarray(cloud if cloud is not None else np.full(n, 100.0))[:, None, None],
        vertical_velocity_pas=np.asarray(omega if omega is not None else np.zeros(n))[:, None, None],
        cloud_ice_kgkg=np.zeros((n, 1, 1)), rain_water_kgkg=np.zeros((n, 1, 1)),
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


class CloudCompositeTests(unittest.TestCase):
    def test_godske_alone_never_creates_cloud_icing(self):
        for cloud, water in (([0, 0], [.1, .1]), ([100, 100], [0, 0]), ([0, 0], [0, 0])):
            fields = compute_icing_fields(**column([-10, -10], cloud=cloud, water=water))
            self.assertGreater(fields["godske20_sum_gpm"][0, 0], 0)
            for product in ("composite", "godske_sum", "slw_max", "slwp"):
                self.assertEqual(select_icing_field(fields, product)[0, 0], 0, product)

    def test_cloud_and_water_must_coincide_vertically(self):
        args = column([-10] * 6, cloud=[100, 100, 0, 0, 0, 0], water=[0, 0, 0, 0, .1, .1])
        fields = compute_icing_fields(**args)
        self.assertEqual(fields["cloud_liquid_sum_gpm"][0, 0], 0)
        self.assertEqual(fields["cloud_godske_sum_gpm"][0, 0], 0)
        self.assertEqual(fields["composite_index"][0, 0], 0)
        self.assertTrue(fields["cloud_input_conflict"][0, 0])

    def test_cloud_godske_geometry_is_intersection_not_column_mask(self):
        args = column([-10] * 4, margin=[-1, -1, 1, 1], cloud=[0, 0, 100, 100],
                      water=[.1] * 4, heights=[0, 1000, 2000, 3000])
        fields = compute_icing_fields(**args)
        # Cloud starts immediately above z=1000; G ends at z=1500.
        self.assertAlmostEqual(fields["cloud_godske_sum_gpm"][0, 0], 500, places=8)
        self.assertAlmostEqual(fields["cloud_godske_base_first_gpm"][0, 0], 1000, places=8)
        self.assertAlmostEqual(fields["cloud_godske_top_first_gpm"][0, 0], 1500, places=8)
        self.assertAlmostEqual(fields["godske20_sum_gpm"][0, 0], 1500, places=8)

    def test_cloud_water_between_samples_is_not_lost(self):
        args = column([10, -10], cloud=[100, 0], water=[.1, .1], heights=[0, 1000])
        fields = compute_icing_fields(**args)
        self.assertFalse(fields["cloud_liquid_eligible"].any())
        self.assertAlmostEqual(fields["cloud_liquid_sum_gpm"][0, 0], 500)
        self.assertAlmostEqual(fields["cloud_max_slwc_gm3"][0, 0], .1)
        self.assertGreater(fields["cloud_slwp_gm2"][0, 0], 0)
        self.assertGreater(fields["composite_index"][0, 0], 0)

    def test_missing_cloud_is_unknown_and_never_falls_back_to_humidity(self):
        args = column([-10, -10])
        args.pop("cloud_fraction_pct")
        fields = compute_icing_fields(**args)
        self.assertGreater(fields["godske20_sum_gpm"][0, 0], 0)
        self.assertGreater(fields["max_slwc_gm3"][0, 0], 0)
        for product in ("composite", "godske_sum", "slw_max", "slwp", "agreement"):
            self.assertTrue(np.isnan(select_icing_field(fields, product)[0, 0]), product)
        self.assertTrue(fields["cloud_profile_missing"][0, 0])

    def test_missing_omega_does_not_change_water_but_prevents_index(self):
        args = column([-10, -10])
        args["vertical_velocity_pas"][:] = np.nan
        fields = compute_icing_fields(**args)
        self.assertTrue(np.isnan(fields["composite_index"][0, 0]))
        self.assertTrue(fields["composite_profile_missing"][0, 0])
        self.assertGreater(fields["cloud_godske_sum_gpm"][0, 0], 0)
        self.assertGreater(fields["cloud_slwp_gm2"][0, 0], 0)

    def test_cloud_missing_level_is_not_bridged(self):
        fields = compute_icing_fields(**column([-10] * 5, cloud=[100, 100, np.nan, 100, 100]))
        self.assertEqual(fields["cloud_godske_n_layers"][0, 0], 2)
        self.assertAlmostEqual(fields["cloud_godske_sum_gpm"][0, 0], 200)
        self.assertTrue(fields["cloud_profile_missing"][0, 0])
        self.assertTrue(fields["cloud_godske_interior_missing"][0, 0])

    def test_unknown_dewpoint_at_profile_edge_is_recorded(self):
        for edge in (0, -1):
            args = column([-10] * 3)
            args["specific_humidity"][edge] = 0
            fields = compute_icing_fields(**args)
            self.assertFalse(fields["cloud_profile_missing"][0, 0])
            self.assertFalse(fields["cloud_godske_interior_missing"][0, 0])
            self.assertTrue(fields["cloud_godske_profile_missing"][0, 0])

    def test_cloud_cover_is_presence_not_a_water_multiplier(self):
        one = compute_icing_fields(**column([-10, -10], cloud=[1, 1]))
        hundred = compute_icing_fields(**column([-10, -10], cloud=[100, 100]))
        for key in ("cloud_slwp_gm2", "cloud_max_slwc_gm3", "cloud_godske_sum_gpm", "composite_index"):
            np.testing.assert_allclose(one[key], hundred[key], err_msg=key)

    def test_warm_and_zero_plateaus_never_receive_composite(self):
        for t in (0, 0.5, 10):
            fields = compute_icing_fields(**column([t, t]))
            self.assertEqual(fields["composite_index"][0, 0], 0)
            self.assertEqual(fields["cloud_godske_sum_gpm"][0, 0], 0)

    def test_very_cold_cloud_water_is_not_cut_off_by_godske(self):
        fields = compute_icing_fields(**column([-25, -25]))
        self.assertEqual(fields["cloud_godske_sum_gpm"][0, 0], 0)
        self.assertGreater(fields["cloud_slwp_gm2"][0, 0], 0)
        self.assertGreater(fields["composite_index"][0, 0], 0)

    def test_ice_and_rain_never_become_cloud_liquid(self):
        args = column([-10, -10], water=[0, 0])
        args["cloud_ice_kgkg"][:] = 0.001
        args["rain_water_kgkg"][:] = 0.001
        fields = compute_icing_fields(**args)
        self.assertEqual(fields["composite_index"][0, 0], 0)
        self.assertEqual(fields["cloud_godske_sum_gpm"][0, 0], 0)
        self.assertEqual(fields["cloud_slwp_gm2"][0, 0], 0)
        self.assertTrue(fields["cloud_ice_only_present"][0, 0])
        self.assertTrue(fields["cold_rain_present"][0, 0])

    def test_water_path_integrates_only_cloud_godske_intersection(self):
        args = column([-10] * 3, margin=[-1, 1, 1], pressure=[1000, 900, 800],
                      cloud=[100] * 3, water=[.1] * 3)
        args["cloud_water_kgkg"][:] = 1e-4
        fields = compute_icing_fields(**args)
        self.assertAlmostEqual(fields["cloud_godske_slwp_gm2"][0, 0], 1000 / G0 * 1e-4 * 5000, places=8)
        self.assertAlmostEqual(fields["cloud_slwp_gm2"][0, 0], 1000 / G0 * 1e-4 * 20000, places=8)

    def test_godske_inclusive_zero_boundary_survives_roundoff(self):
        fields = compute_icing_fields(**column([-5, -5], margin=[0, 0]))
        self.assertAlmostEqual(fields["godske20_sum_gpm"][0, 0], 100)
        self.assertAlmostEqual(fields["cloud_godske_sum_gpm"][0, 0], 100)

    def test_invalid_cloud_range_and_new_field_shape_are_rejected(self):
        for cover in (-1, 101):
            fields = compute_icing_fields(**column([-10, -10], cloud=[cover, cover]))
            self.assertTrue(np.isnan(fields["composite_index"][0, 0]))
        args = column([-10, -10])
        args["vertical_velocity_pas"] = np.zeros((2, 3))
        with self.assertRaises(ValueError):
            compute_icing_fields(**args)

    def test_sfip_membership_knots_and_units(self):
        mt, mrh, mw, ml = sfip_memberships(
            np.array([-28., -12., -1., 1.]), np.array([60., 77.5, 95., 100.]),
            np.array([0., .0001, .0004, .001]), np.array([-.1, 0., .05, .2]),
        )
        np.testing.assert_allclose(mt, [0, 1, 1, 0])
        np.testing.assert_allclose(mrh, [0, .25, 1, 1])
        np.testing.assert_allclose(mw, [-.4, 0, 1, 1])
        np.testing.assert_allclose(ml, [0, .25, 1, 1])
        self.assertAlmostEqual(float(sfip_index(-10., 95., .0004, .05)), 100.)
        self.assertAlmostEqual(float(sfip_index(-10., 95., .0004, 0.)), 80.)
        self.assertAlmostEqual(float(sfip_index(-10., 95., .0004, -.1)), 72.)
        self.assertEqual(float(sfip_index(-10., 60., 0., -.1)), 0.)
        self.assertTrue(np.isnan(sfip_index(-10., 95., np.nan, 0.)))

    def test_omega_sign_and_density_conversion(self):
        args = column([-10, -10], omega=[-0.5, 0.5])
        fields = compute_icing_fields(**args)
        expected = -args["vertical_velocity_pas"] / (fields["air_density_kgm3"] * G0)
        np.testing.assert_allclose(fields["upward_velocity_ms"], expected)
        self.assertGreater(fields["upward_velocity_ms"][0, 0, 0], 0)
        self.assertLess(fields["upward_velocity_ms"][1, 0, 0], 0)
        self.assertGreater(fields["composite_index_level"][0, 0, 0], fields["composite_index_level"][1, 0, 0])

    def test_composite_finds_interior_cubic_maximum(self):
        args = column([-28, -12], heights=[0, 3000])
        t = args["temperature_k"] - 273.15
        e = np.array([95., 60.])[:, None, None] / 100 * 610.94 * np.exp(17.625*t/(243.04+t))
        args["specific_humidity"] = .622*e / (args["pressure_hpa"][:, None, None]*100 - .378*e)
        args["cloud_water_kgkg"][:] = .000004
        fields = compute_icing_fields(**args)
        # s*(.35*(1-s)^2 + .0045) has its maximum strictly inside 0..1.
        root = (1.4 - np.sqrt(1.4**2 - 4*1.05*.3545)) / (2*1.05)
        expected = 100*root*(.35*(1-root)**2 + .0045)
        self.assertAlmostEqual(fields["composite_index"][0, 0], expected, places=10)
        self.assertGreater(fields["composite_index"][0, 0], fields["composite_index_level"].max())

    def test_increasing_water_cannot_lower_composite_when_cloud_gate_expands(self):
        args = column([-70, -.1], water=[0, .0012], heights=[0, 6000])
        t = args["temperature_k"] - 273.15
        e = .95 * 610.94 * np.exp(17.625*t/(243.04+t))
        args["specific_humidity"] = .622*e / (args["pressure_hpa"][:, None, None]*100 - .378*e)
        first = compute_icing_fields(**args)
        args["cloud_water_kgkg"] *= 4
        second = compute_icing_fields(**args)
        self.assertGreaterEqual(second["composite_index"][0, 0], first["composite_index"][0, 0])


if __name__ == "__main__":
    unittest.main()
