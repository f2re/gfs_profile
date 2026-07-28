from __future__ import annotations

import unittest

import pandas as pd

from gfs_core import freezing_level_diagnostic, freezing_level_m


class FreezingLevelTests(unittest.TestCase):
    def test_freezing_level_found(self) -> None:
        df = pd.DataFrame({"temperature_c": [5.0, -5.0], "geopotential_height_m": [1000.0, 2000.0]})
        self.assertAlmostEqual(freezing_level_m(df), 1500.0, places=1)
        diagnostic = freezing_level_diagnostic(df)
        self.assertEqual(diagnostic["status"], "found")
        self.assertAlmostEqual(float(diagnostic["height_m"]), 1500.0, places=1)

    def test_freezing_level_below_profile(self) -> None:
        df = pd.DataFrame({"temperature_c": [-5.0, -10.0], "geopotential_height_m": [100.0, 1000.0]})
        self.assertEqual(freezing_level_diagnostic(df)["status"], "below_lowest_level")

    def test_freezing_level_above_profile(self) -> None:
        df = pd.DataFrame({"temperature_c": [5.0, 2.0], "geopotential_height_m": [100.0, 1000.0]})
        self.assertEqual(freezing_level_diagnostic(df)["status"], "above_highest_level")


if __name__ == "__main__":
    unittest.main()
