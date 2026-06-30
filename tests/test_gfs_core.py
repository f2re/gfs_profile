from __future__ import annotations

import unittest

import pandas as pd

from gfs_core import (
    add_derived_parameters,
    canonical_leads,
    freezing_level_m,
    grib_filter_url,
    run_file_name,
    snap_to_gfs_grid,
    validate_lead,
)


class GfsCoreTests(unittest.TestCase):
    def test_snap_to_gfs_grid(self) -> None:
        self.assertEqual(snap_to_gfs_grid(59.93, 30.31), (60.0, 30.25))
        self.assertEqual(snap_to_gfs_grid(-12.12, -45.38), (-12.0, -45.5))

    def test_canonical_leads(self) -> None:
        leads = canonical_leads()
        self.assertIn(0, leads)
        self.assertIn(120, leads)
        self.assertIn(123, leads)
        self.assertIn(384, leads)
        self.assertNotIn(122, leads)
        self.assertEqual(validate_lead(24), 24)

    def test_run_file_name(self) -> None:
        self.assertEqual(run_file_name("06", 24), "gfs.t06z.pgrb2.0p25.f024")

    def test_grib_filter_url_contains_required_subset_params(self) -> None:
        url = grib_filter_url("20260630", "06", 24, 55.75, 37.5)
        self.assertIn("filter_gfs_0p25_1hr.pl", url)
        self.assertIn("file=gfs.t06z.pgrb2.0p25.f024", url)
        self.assertIn("var_TMP=on", url)
        self.assertIn("var_RH=on", url)
        self.assertIn("var_UGRD=on", url)
        self.assertIn("var_VGRD=on", url)
        self.assertIn("var_HGT=on", url)
        self.assertIn("all_lev=on", url)

    def test_add_derived_parameters(self) -> None:
        df = pd.DataFrame(
            {
                "pressure_hpa": [1000.0, 850.0],
                "temperature_k": [273.15, 263.15],
                "relative_humidity_pct": [80.0, 60.0],
                "u_wind_ms": [3.0, 0.0],
                "v_wind_ms": [4.0, -5.0],
                "geopotential_height_m": [100.0, 1500.0],
            }
        )
        out = add_derived_parameters(df)
        self.assertIn("temperature_c", out.columns)
        self.assertIn("dewpoint_c", out.columns)
        self.assertIn("theta_k", out.columns)
        self.assertAlmostEqual(float(out.loc[0, "wind_speed_ms"]), 5.0, places=3)

    def test_wind_direction_is_meteorological_from_direction(self) -> None:
        df = pd.DataFrame(
            {
                "pressure_hpa": [1000.0, 925.0, 850.0, 700.0],
                "temperature_k": [273.15, 273.15, 273.15, 273.15],
                "relative_humidity_pct": [80.0, 80.0, 80.0, 80.0],
                "u_wind_ms": [10.0, -10.0, 0.0, 0.0],
                "v_wind_ms": [0.0, 0.0, 10.0, -10.0],
                "geopotential_height_m": [100.0, 800.0, 1500.0, 3000.0],
            }
        )
        out = add_derived_parameters(df)
        self.assertAlmostEqual(float(out.loc[0, "wind_dir_deg"]), 270.0, places=3)
        self.assertAlmostEqual(float(out.loc[1, "wind_dir_deg"]), 90.0, places=3)
        self.assertAlmostEqual(float(out.loc[2, "wind_dir_deg"]), 180.0, places=3)
        self.assertAlmostEqual(float(out.loc[3, "wind_dir_deg"]), 0.0, places=3)

    def test_freezing_level_interpolation(self) -> None:
        df = pd.DataFrame(
            {
                "temperature_c": [5.0, -5.0],
                "geopotential_height_m": [1000.0, 2000.0],
            }
        )
        self.assertAlmostEqual(freezing_level_m(df), 1500.0, places=1)


if __name__ == "__main__":
    unittest.main()
