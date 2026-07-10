from __future__ import annotations

import unittest

import numpy as np

from route_profile_simple_overlay import symbol_positions


class RouteProfileSimpleOverlayTests(unittest.TestCase):
    def test_empty_mask_has_no_symbols(self) -> None:
        mask = np.zeros((6, 8), dtype=bool)
        self.assertEqual(symbol_positions(mask, max_symbols=12), ())

    def test_symbols_stay_inside_active_mask(self) -> None:
        mask = np.zeros((8, 10), dtype=bool)
        mask[1:7, 2:9] = True
        positions = symbol_positions(mask, max_symbols=16)
        self.assertGreater(len(positions), 1)
        self.assertLessEqual(len(positions), 16)
        for row, col in positions:
            self.assertTrue(mask[row, col])

    def test_symbol_count_is_capped_for_large_zone(self) -> None:
        mask = np.ones((20, 30), dtype=bool)
        positions = symbol_positions(mask, max_symbols=18)
        self.assertEqual(len(positions), 18)

    def test_small_zone_keeps_at_least_one_symbol(self) -> None:
        mask = np.zeros((13, 11), dtype=bool)
        mask[5, 7] = True
        self.assertEqual(symbol_positions(mask, max_symbols=10), ((5, 7),))


if __name__ == "__main__":
    unittest.main()
