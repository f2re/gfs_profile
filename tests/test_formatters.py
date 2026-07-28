from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from formatters import format_profile_summary, write_profile_csv
from gfs_core import GfsRun, ProfileResult, add_derived_parameters
from profile_plot import write_profile_png


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

    def test_summary_contains_compact_telegram_fields(self) -> None:
        summary = format_profile_summary(self._result())
        self.assertIn("GFS 0.25", summary)
        self.assertIn("⊞GFS", summary)
        self.assertIn("<pre>", summary)
        self.assertIn("pгПа Zgкм", summary)
        self.assertIn("T/Td°C", summary)
        self.assertIn("❄ 0/-10/-20°C", summary)
        self.assertIn("🌬 max", summary)
        self.assertIn("NOMADS subset", summary)
        self.assertIn("MSL", summary)
        self.assertNotIn("температура", summary)
        self.assertNotIn("точка росы", summary)
        self.assertNotIn("Макс. ветер", summary)
        self.assertNotIn("Действительно на", summary)
        self.assertNotIn("Max wind", summary)
        self.assertNotIn("Valid:", summary)

    def test_isotherm_heights_are_interpolated(self) -> None:
        summary = format_profile_summary(self._result())
        self.assertIn("❄ 0/-10/-20°C: 1.5/3.0/4.3 км MSL", summary)

    def test_compact_level_lines_are_short(self) -> None:
        summary = format_profile_summary(self._result())
        table = summary.split("<pre>", 1)[1].split("</pre>", 1)[0]
        level_lines = [line for line in table.splitlines() if line[:1].isdigit()]
        self.assertTrue(level_lines)
        for line in level_lines:
            self.assertLessEqual(len(line), 42)

    def test_csv_is_written(self) -> None:
        path = write_profile_csv(self._result())
        try:
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("pressure_hpa", content)
            self.assertIn("geopotential_height_km", content)
        finally:
            path.unlink(missing_ok=True)

    def test_png_is_written(self) -> None:
        path = write_profile_png(self._result())
        try:
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1024)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
