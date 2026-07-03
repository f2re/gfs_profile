from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gfs_core import GfsRun
from windgram_plot import _height_labels_by_level
from windgram_product import WindgramCell, WindgramData


def cell(lead: int, level: int, height_m: float | None) -> WindgramCell:
    return WindgramCell(
        lead_hour=lead,
        valid_time_utc=datetime(2026, 7, 3, tzinfo=timezone.utc),
        pressure_hpa=level,
        height_m=height_m,
        temperature_c=0.0,
        relative_humidity_pct=50.0,
        u_wind_ms=1.0,
        v_wind_ms=1.0,
        wind_speed_ms=1.4,
        wind_dir_deg=225.0,
    )


class WindgramPlotTest(unittest.TestCase):
    def test_height_labels_use_mean_height_over_all_leads(self) -> None:
        data = WindgramData(
            run=GfsRun("20260703", "00"),
            requested_lat=45.0,
            requested_lon=39.0,
            grid_lat=45.0,
            grid_lon=39.0,
            leads=[0, 6, 12],
            levels_hpa=[850, 700],
            cells=[
                cell(0, 850, 1400.0),
                cell(6, 850, 1600.0),
                cell(12, 850, 1900.0),
                cell(0, 700, 3000.0),
                cell(6, 700, None),
                cell(12, 700, 3300.0),
            ],
            param="wind",
        )

        labels = _height_labels_by_level(data)

        self.assertEqual(labels[850], "850\nZср 1.6 км")
        self.assertEqual(labels[700], "700\nZср 3.1 км")


if __name__ == "__main__":
    unittest.main()
