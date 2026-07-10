from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from cloudgram_product import CloudgramCell
from geocode import GeoPoint
from gfs_core import GfsRun
from route_profile import ROUTE_LEVELS_HPA, RouteProfileData, RouteWaypoint
from route_profile_metric_units import format_wind_speed_ms
from route_profile_plot import _display_groups, _hazard_tokens_for_indices, write_route_profile_png
from telegram_file_send import _png_dimensions


class RouteProfilePlotTests(unittest.TestCase):
    def _sample_data(self, mode: str, n_points: int = 7) -> RouteProfileData:
        run = GfsRun("20260710", "00")
        origin = GeoPoint(55.7558, 37.6173, "Москва", "test")
        destination = GeoPoint(55.0084, 82.9357, "Новосибирск", "test")
        levels = ROUTE_LEVELS_HPA
        distances = np.linspace(0.0, 2800.0, n_points)
        shape = (len(levels), n_points)
        temperature = np.linspace(12.0, -28.0, len(levels))[:, None] + np.linspace(0.0, 4.0, n_points)[None, :]
        humidity = np.full(shape, 65.0)
        humidity[2:5, 1:4] = 94.0
        humidity[7:, :] = 84.0
        wind = np.full(shape, 12.0)
        wind[1:4, :3] = 25.0
        wind[2:5, 3:5] = 36.0
        u = wind * 0.8
        v = wind * 0.4
        height = np.repeat(np.linspace(100.0, 5800.0, len(levels))[:, None], n_points, axis=1)
        icing = np.zeros(shape, dtype=int)
        icing[2:5, 1:4] = 2
        turbulence = np.zeros(shape, dtype=int)
        turbulence[5:9, 3:6] = 2
        cloud_mask = humidity >= 80.0
        point_risk = np.asarray([0, 1, 1, 2, 3, 2, 1][:n_points], dtype=int)

        waypoints: list[RouteWaypoint] = []
        for index, distance in enumerate(distances):
            storm = index == min(4, n_points - 1)
            surface = CloudgramCell(
                lead_hour=6 + index,
                valid_time_utc=datetime(2026, 7, 10, 6, tzinfo=timezone.utc) + timedelta(hours=index),
                high_cloud_pct=80.0,
                mid_cloud_pct=70.0,
                low_cloud_pct=60.0,
                total_cloud_pct=90.0,
                ceiling_m=650.0 if storm else 3500.0,
                precip_mm=4.0 if storm else 0.0,
                precip_rate_mmh=2.0 if storm else 0.0,
                conv_precip_mm=1.0 if storm else 0.0,
                precip_type="R" if storm else "—",
                cape_jkg=800.0 if storm else 50.0,
                cin_jkg=-20.0,
                cb_score=3 if storm else 0,
                visibility_km=4.0 if storm else 20.0,
                phenomena="TSRA" if storm else "—",
                hazard_score=4 if storm else 0,
                hazard_text="гроза" if storm else "спокойно",
            )
            waypoints.append(
                RouteWaypoint(
                    index=index,
                    fraction=index / max(1, n_points - 1),
                    lat=55.0,
                    lon=37.0 + index,
                    distance_km=float(distance),
                    elapsed_hours=float(index),
                    lead_hour=6 + index,
                    valid_time_utc=surface.valid_time_utc,
                    grid_lat=55.0,
                    grid_lon=37.0 + index,
                    profile=pd.DataFrame(),
                    surface=surface,
                    risk_score=int(point_risk[index]),
                    risk_reasons=("test",),
                )
            )

        return RouteProfileData(
            run=run,
            origin=origin,
            destination=destination,
            departure_lead=6,
            speed_kmh=300,
            mode=mode,
            total_distance_km=2800.0,
            duration_hours=9.3,
            levels_hpa=levels,
            waypoints=tuple(waypoints),
            temperature_c=temperature,
            humidity_pct=humidity,
            wind_speed_ms=wind,
            wind_dir_deg=np.zeros(shape),
            u_wind_ms=u,
            v_wind_ms=v,
            height_m=height,
            icing_score=icing,
            turbulence_score=turbulence,
            cloud_mask=cloud_mask,
            point_risk=point_risk,
        )

    def test_display_groups_are_limited_and_cover_route(self) -> None:
        data = self._sample_data("simple", n_points=7)
        groups = _display_groups(data, max_cards=3)
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].start_index, 0)
        self.assertEqual(groups[-1].end_index, len(data.waypoints) - 1)
        covered_legs = set()
        for group in groups:
            covered_legs.update(range(group.start_index, group.end_index))
        self.assertEqual(covered_legs, set(range(len(data.waypoints) - 1)))

    def test_hazard_tokens_prioritize_key_flight_risks(self) -> None:
        data = self._sample_data("simple")
        keys = {token.key for token in _hazard_tokens_for_indices(data, range(len(data.waypoints)), limit=4)}
        self.assertIn("thunder", keys)
        self.assertIn("icing", keys)
        self.assertIn("turbulence", keys)
        self.assertIn("wind", keys)

    def test_wind_speed_label_uses_si_units(self) -> None:
        self.assertEqual(format_wind_speed_ms(25.4), "25 м/с")
        self.assertNotIn("уз", format_wind_speed_ms(25.4))

    def test_simple_and_professional_png_are_telegram_safe(self) -> None:
        paths: list[Path] = []
        try:
            for mode in ("simple", "pro"):
                path = write_route_profile_png(self._sample_data(mode))
                paths.append(path)
                payload = path.read_bytes()
                self.assertGreater(len(payload), 10_000)
                dimensions = _png_dimensions(payload)
                self.assertIsNotNone(dimensions)
                width, height = dimensions or (0, 0)
                self.assertGreater(width, height)
                self.assertLessEqual(width + height, 10_000)
                self.assertLessEqual(max(width, height) / min(width, height), 20.0)
        finally:
            for path in paths:
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
