"""Presentation must preserve physical quantities when changing visible units."""
import unittest

import numpy as np

from icing_config import ICING_PRODUCTS
from icing_meteorology import PRODUCT_UNITS
from icing_presentation import (
    DISPLAY_UNITS, PRODUCT_EXPLANATIONS, PRODUCT_HINTS,
    display_icing_field, method_help_text, icing_profile_missing,
)


class IcingPresentationTests(unittest.TestCase):
    def test_vertical_values_are_converted_without_mutating_raw_heights(self):
        fields = {
            "cloud_godske_sum_gpm": np.array([[1500., np.nan]]),
            "cloud_godske_max_layer_gpm": np.array([[500., np.nan]]),
            "cloud_godske_base_first_gpm": np.array([[1200., np.nan]]),
            "cloud_godske_top_first_gpm": np.array([[1700., np.nan]]),
            "cloud_godske_base_first_open": np.array([[False, True]]),
            "cloud_godske_top_first_open": np.array([[False, True]]),
            "cloud_max_slwc_height_gpm": np.array([[1800., np.nan]]),
        }
        original = {key: value.copy() for key, value in fields.items()}
        for product, expected in (("godske_sum", 150), ("godske_max", 50),
                                  ("godske_base", 120), ("godske_top", 170),
                                  ("slw_height", 180)):
            with self.subTest(product=product):
                shown = display_icing_field(fields, product)
                np.testing.assert_equal(shown, [[expected, np.nan]])
                self.assertEqual(DISPLAY_UNITS[product], "дам")
                self.assertEqual(PRODUCT_UNITS[product], "гп.м")
        for key in fields:
            np.testing.assert_equal(fields[key], original[key])

    def test_water_and_categories_keep_their_numerical_scale(self):
        fields = {"cloud_max_slwc_gm3": np.array([[.2]]), "cloud_slwp_gm2": np.array([[120.]]),
                  "cloud_godske_n_layers": np.array([[2.]]), "agreement": np.array([[4.]]),
                  "composite_index": np.array([[63.]])}
        for product, expected in (("slw_max", .2), ("slwp", 120),
                                  ("godske_count", 2), ("agreement", 4), ("composite", 63)):
            with self.subTest(product=product):
                self.assertEqual(display_icing_field(fields, product)[0, 0], expected)

    def test_each_product_explains_the_quantity_and_sources_within_message_limit(self):
        self.assertEqual(set(ICING_PRODUCTS), set(PRODUCT_HINTS))
        self.assertEqual(set(ICING_PRODUCTS), set(PRODUCT_EXPLANATIONS))
        for product in (None, *ICING_PRODUCTS):
            with self.subTest(product=product):
                text = method_help_text(product)
                self.assertLess(len(text), 4000)
                self.assertIn("https://nomads.ncep.noaa.gov/", text)
                self.assertIn("Natural Earth", text)
                self.assertNotIn("дождевая фаза", text)
                self.assertNotIn("M8D20", text)
        maximum = method_help_text("slw_max")
        self.assertIn("1000 до 300 гПа", maximum)
        self.assertIn("одному выбранному сроку", maximum)
        self.assertIn("0,2 грамма", maximum)

    def test_composite_help_explains_scale_same_height_and_sources(self):
        text = method_help_text("composite")
        self.assertIn("индекс, а не вероятность", text)
        self.assertIn("на одном уровне", text)
        self.assertIn("Уровень максимума индекса может отличаться", text)
        self.assertIn("0,001 г/м³", text)
        self.assertIn("Па/с в м/с", text)
        self.assertIn("10.1002/met.1505", text)
        self.assertIn("EGU2018-8415", text)
        self.assertEqual(DISPLAY_UNITS["composite"], "индекс 0–100")

    def test_godske_quality_keeps_thermodynamic_gaps_separate_from_water(self):
        fields = {"cloud_profile_missing": np.array([[True, False, False]]),
                  "cloud_godske_profile_missing": np.array([[True, True, False]]),
                  "composite_profile_missing": np.array([[True, False, True]])}
        np.testing.assert_array_equal(icing_profile_missing(fields, "godske_sum"), [[True, True, False]])
        np.testing.assert_array_equal(icing_profile_missing(fields, "slw_max"), [[True, False, False]])
        np.testing.assert_array_equal(icing_profile_missing(fields, "composite"), [[True, False, True]])


if __name__ == "__main__":
    unittest.main()
