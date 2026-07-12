from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from scipy.ndimage import label

from cloudgram_product import CloudgramCell
from geocode import GeoPoint
from gfs_core import GfsRun
from route_profile import ROUTE_LEVELS_HPA, RouteProfileData, RouteWaypoint
from route_profile_smoothing import build_route_display_grid, route_bearings_deg


class RouteProfileSmoothingTests(unittest.TestCase):
    def _data(self, mode: str = "simple") -> RouteProfileData:
        count = 9
        levels = ROUTE_LEVELS_HPA
        shape = (len(levels), count)
        x = np.linspace(0.0, 800.0, count)
        zigzag = np.asarray([0.0, 6.0, -4.0, 7.0, -5.0, 5.0, -3.0, 4.0, 0.0])
        temperature = np.linspace(12.0, -28.0, len(levels))[:, None] + zigzag[None, :]
        humidity = np.full(shape, 55.0)
        humidity[3:8, 2:7] = 94.0
        wind = np.full(shape, 15.0)
        wind[6:10, 3:8] = 28.0
        icing = np.zeros(shape, dtype=int)
        icing[4:8, 2:6] = 2
        turbulence = np.zeros(shape, dtype=int)
        turbulence[7:11, 4:8] = 2
        height = np.repeat(np.linspace(100.0, 5800.0, len(levels))[:, None], count, axis=1)
        waypoints = []
        for index, distance in enumerate(x):
            surface = CloudgramCell(
                lead_hour=6 + index,
                valid_time_utc=datetime(2026, 7, 10, 6, tzinfo=timezone.utc) + timedelta(hours=index),
                high_cloud_pct=50.0,
                mid_cloud_pct=60.0,
                low_cloud_pct=70.0,
                total_cloud_pct=80.0,
                ceiling_m=2500.0,
                precip_mm=0.0,
                precip_rate_mmh=0.0,
                conv_precip_mm=0.0,
                precip_type="—",
                cape_jkg=0.0,
                cin_jkg=-100.0,
                cb_score=0,
                visibility_km=20.0,
                phenomena="—",
                hazard_score=0,
                hazard_text="спокойно",
            )
            waypoints.append(RouteWaypoint(index, index / (count - 1), 55.0 + index * 0.05, 37.0 + index * 0.12, float(distance), float(index), 6 + index, surface.valid_time_utc, 55.0, 37.0, pd.DataFrame(), surface, 1, ("test",)))
        return RouteProfileData(
            run=GfsRun("20260710", "00"),
            origin=GeoPoint(55.75, 37.62, "Москва", "test"),
            destination=GeoPoint(59.94, 30.31, "Санкт-Петербург", "test"),
            departure_lead=6,
            speed_kmh=300,
            mode=mode,
            total_distance_km=800.0,
            duration_hours=2.7,
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
            point_risk=np.ones(count, dtype=int),
        )

    def test_simple_display_grid_is_denser_than_professional(self) -> None:
        data = self._data()
        simple = build_route_display_grid(data, "simple")
        professional = build_route_display_grid(data, "pro")
        self.assertGreater(simple.x_km.size, professional.x_km.size)
        self.assertGreater(simple.pressure_hpa.size, professional.pressure_hpa.size)

    def test_probability_fields_are_bounded(self) -> None:
        grid = build_route_display_grid(self._data(), "simple")
        for field in (grid.cloud_intensity, grid.icing_intensity, grid.turbulence_intensity):
            self.assertGreaterEqual(float(np.nanmin(field)), 0.0)
            self.assertLessEqual(float(np.nanmax(field)), 1.0)

    def test_simple_smoothing_reduces_route_zigzag(self) -> None:
        data = self._data()
        source = data.temperature_c[0]
        smoothed = build_route_display_grid(data, "simple").temperature_c[-1]
        source_roughness = float(np.mean(np.abs(np.diff(source, n=2))))
        display_roughness = float(np.mean(np.abs(np.diff(smoothed, n=2))))
        self.assertLess(display_roughness, source_roughness)

    def test_disconnected_hazard_areas_are_not_bridged(self) -> None:
        data = self._data()
        data.icing_score[:, :] = 0
        data.icing_score[4:7, 1:3] = 2
        data.icing_score[4:7, 6:8] = 2
        grid = build_route_display_grid(data, "simple")
        _, count = label(grid.icing_intensity >= 0.48, structure=np.ones((3, 3), dtype=int))
        self.assertGreaterEqual(count, 2)

    def test_display_grid_does_not_mutate_objective_risk(self) -> None:
        data = self._data()
        before = data.point_risk.copy()
        build_route_display_grid(data, "simple")
        np.testing.assert_array_equal(data.point_risk, before)

    def test_route_bearings_match_waypoint_count(self) -> None:
        data = self._data()
        bearings = route_bearings_deg(data)
        self.assertEqual(len(bearings), len(data.waypoints))
        self.assertTrue(np.all(np.isfinite(bearings)))


if __name__ == "__main__":
    unittest.main()
