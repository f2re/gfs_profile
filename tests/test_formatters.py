from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from formatters import format_profile_summary, write_profile_csv
from gfs_core import GfsRun, ProfileResult, add_derived_parameters


class FormatterTests(unittest.TestCase):
    def _result(self) -> ProfileResult:
        df = pd.DataFrame(
            {
                "pressure_hpa": [1000.0, 925.0, 850.0, 700.0, 500.0, 300.0],
                "temperature_k": [285.15, 280.15, 273.15, 263.15, 243.15, 223.15],
                "relative_humidity_pct": [70.0, 80.0, 90.0, 50.0, 40.0, 30.0],
                "u_wind_ms": [1.0, 2.0, 4.0, 6.0, 10.0, 20.0],
                "v_wind_ms": [0.0, 1.0, 3.0, 6.0, 5.0, 10.0],
                "geopotential_height_m": [100.0, 800.0, 1500.0, 3000.0, 5600.0, 9000.0],
            }
        )
        return ProfileResult(
            run=GfsRun("20260630", "06"),
            lead_hour=24,
            requested_lat=59.93,
            requested_lon=30.31,
            grid_lat=60.0,
            grid_lon=30.25,
            grib_path=Path("dummy.grib2"),
            dataframe=add_derived_parameters(df),
        )

    def test_summary_contains_operational_fields(self) -> None:
        summary = format_profile_summary(self._result())
        self.assertIn("GFS 0.25", summary)
        self.assertIn("Узел GFS", summary)
        self.assertIn("Max wind", summary)

    def test_csv_is_written(self) -> None:
        path = write_profile_csv(self._result())
        try:
            self.assertTrue(path.exists())
            self.assertIn("pressure_hpa", path.read_text(encoding="utf-8"))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
