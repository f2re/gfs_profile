from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from geocode import GeoPoint
from gfs_core import GfsRun
from route_profile import ROUTE_LEVELS_HPA, RouteProfileData
from route_profile_rendering import write_route_profile_png
from telegram_file_send import _png_dimensions


class RouteProfileRenderPngTests(unittest.TestCase):
    def _data(self, mode: str) -> RouteProfileData:
        count = 12
        levels = ROUTE_LEVELS_HPA
        shape = (len(levels), count)
        distances = np.linspace(0.0, 1200.0, count)
        temperature = np.linspace(15.0, -30.0, len(levels))[:, None] + 3.0 * np.sin(np.linspace(0.0, 3.0 * np.pi, count))[None, :]
        humidity = np.full(shape, 60.0); humidity[3:8, 2:7] = 94.0; humidity[8:12, 7:11] = 86.0
        wind = np.full(shape, 14.0); wind[2:6, 1:6] = 27.0; wind[6:10, 7:11] = 34.0
        icing = np.zeros(shape, dtype=int); icing[4:8, 2:6] = 2
        turbulence = np.zeros(shape, dtype=int); turbulence[7:11, 6:10] = 2
        height = np.repeat(np.linspace(100.0, 5800.0, len(levels))[:, None], count, axis=1)
        risks = np.asarray([0, 0, 1, 1, 1, 2, 2, 2, 3, 2, 1, 1], dtype=int)
        waypoints = []
        for index, distance in enumerate(distances):
            storm = index == 8
            surface = SimpleNamespace(
                cb_score=3 if storm else 0,
                phenomena="TSRA" if storm else "—",
                precip_mm=3.0 if storm else 0.0,
                visibility_km=7.0 if storm else 20.0,
                ceiling_m=800.0 if storm else 3500.0,
            )
            waypoints.append(
                SimpleNamespace(
                    index=index,
                    distance_km=float(distance),
                    lead_hour=6 + index,
                    valid_time_utc=datetime(2026, 7, 10, 6, tzinfo=timezone.utc) + timedelta(hours=index),
                    risk_score=int(risks[index]),
                    surface=surface,
                    lat=55.0 + index * 0.03,
                    lon=37.0 + index * 0.08,
                )
            )
        return RouteProfileData(
            run=GfsRun("20260710", "00"),
            origin=GeoPoint(55.7558, 37.6173, "Москва", "test"),
            destination=GeoPoint(55.0084, 82.9357, "Новосибирск", "test"),
            departure_lead=6,
            speed_kmh=300,
            mode=mode,
            total_distance_km=1200.0,
            duration_hours=4.0,
            levels_hpa=levels,
            waypoints=tuple(waypoints),
            temperature_c=temperature,
            humidity_pct=humidity,
            wind_speed_ms=wind,
            wind_dir_deg=np.zeros(shape),
            u_wind_ms=wind * 0.8,
            v_wind_ms=wind * 0.3,
            height_m=height,
            icing_score=icing,
            turbulence_score=turbulence,
            cloud_mask=humidity >= 80.0,
            point_risk=risks,
        )

    def test_both_modes_render_telegram_safe_png(self) -> None:
        paths: list[Path] = []
        sizes: dict[str, int] = {}
        try:
            for mode in ("simple", "pro"):
                path = write_route_profile_png(self._data(mode))
                paths.append(path)
                payload = path.read_bytes()
                sizes[mode] = len(payload)
                self.assertGreater(len(payload), 10_000)
                dimensions = _png_dimensions(payload)
                self.assertIsNotNone(dimensions)
                width, height = dimensions or (0, 0)
                self.assertGreater(width, height)
                self.assertLessEqual(width + height, 10_000)
                self.assertLessEqual(max(width, height) / min(width, height), 20.0)
            self.assertNotEqual(sizes["simple"], sizes["pro"])
        finally:
            for path in paths:
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
