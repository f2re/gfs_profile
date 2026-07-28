from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gfs_core import GfsRun
from windgram_product import WindgramCell, WindgramData, normalize_windgram_param, windgram_leads, windgram_matrices


class WindgramTests(unittest.TestCase):
    def test_leads_with_six_hour_step(self) -> None:
        self.assertEqual(windgram_leads(0, 24, 6), [0, 6, 12, 18, 24])

    def test_leads_skip_non_gfs_hours_after_120(self) -> None:
        self.assertEqual(windgram_leads(120, 132, 6), [120, 126, 132])

    def test_param_aliases(self) -> None:
        self.assertEqual(normalize_windgram_param("wind"), "wind")
        self.assertEqual(normalize_windgram_param("temp"), "temp")
        self.assertEqual(normalize_windgram_param("rh"), "rh")
        self.assertEqual(normalize_windgram_param("влажность"), "rh")

    def test_matrices_include_temperature_and_humidity(self) -> None:
        valid = datetime(2026, 7, 1, tzinfo=timezone.utc)
        data = WindgramData(
            run=GfsRun("20260701", "00"),
            requested_lat=45.0,
            requested_lon=39.0,
            grid_lat=45.0,
            grid_lon=39.0,
            leads=[0],
            levels_hpa=[1000],
            cells=[
                WindgramCell(
                    lead_hour=0,
                    valid_time_utc=valid,
                    pressure_hpa=1000,
                    height_m=100.0,
                    temperature_c=21.5,
                    relative_humidity_pct=78.0,
                    u_wind_ms=1.0,
                    v_wind_ms=2.0,
                    wind_speed_ms=2.2,
                    wind_dir_deg=240.0,
                )
            ],
            param="temp",
        )
        speed, direction, u, v, temperature, humidity = windgram_matrices(data)
        self.assertAlmostEqual(float(speed[0, 0]), 2.2)
        self.assertAlmostEqual(float(direction[0, 0]), 240.0)
        self.assertAlmostEqual(float(u[0, 0]), 1.0)
        self.assertAlmostEqual(float(v[0, 0]), 2.0)
        self.assertAlmostEqual(float(temperature[0, 0]), 21.5)
        self.assertAlmostEqual(float(humidity[0, 0]), 78.0)


if __name__ == "__main__":
    unittest.main()
