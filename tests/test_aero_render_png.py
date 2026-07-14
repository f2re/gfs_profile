from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from aero_plot import write_aero_png
from gfs_core import GfsRun, ProfileResult
from telegram_file_send import _png_dimensions


class AeroRenderPngTests(unittest.TestCase):
    def test_unified_aero_png_is_telegram_safe(self) -> None:
        pressure = np.asarray([1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100], dtype=float)
        height = np.linspace(120.0, 15800.0, pressure.size)
        temperature = 17.0 - 6.2 * height / 1000.0
        dewpoint = temperature - np.linspace(2.0, 14.0, pressure.size)
        u = np.linspace(2.0, 32.0, pressure.size)
        v = np.linspace(-3.0, 18.0, pressure.size)
        speed = np.sqrt(u**2 + v**2)
        temperature_k = temperature + 273.15
        theta = temperature_k * np.power(1000.0 / pressure, 0.2854)
        rh = np.clip(95.0 - (temperature - dewpoint) * 5.0, 20.0, 100.0)
        frame = pd.DataFrame(
            {
                "pressure_hpa": pressure,
                "temperature_c": temperature,
                "temperature_k": temperature_k,
                "dewpoint_c": dewpoint,
                "relative_humidity_pct": rh,
                "u_wind_ms": u,
                "v_wind_ms": v,
                "wind_speed_ms": speed,
                "geopotential_height_m": height,
                "geopotential_height_km": height / 1000.0,
                "theta_k": theta,
            }
        )
        result = ProfileResult(
            run=GfsRun("20260714", "00"),
            lead_hour=12,
            requested_lat=59.939,
            requested_lon=30.316,
            grid_lat=60.0,
            grid_lon=30.25,
            grib_path=Path("synthetic.grib2"),
            dataframe=frame,
        )
        path = write_aero_png(result)
        try:
            payload = path.read_bytes()
            self.assertGreater(len(payload), 20_000)
            dimensions = _png_dimensions(payload)
            self.assertIsNotNone(dimensions)
            width, height_px = dimensions or (0, 0)
            self.assertGreater(width, height_px)
            self.assertLessEqual(width + height_px, 10_000)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
