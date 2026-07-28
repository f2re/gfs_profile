from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from cloudgram_product import CloudgramCell
from geocode import GeoPoint
from gfs_core import GfsRun, ProfileResult
from route_profile import (
    ROUTE_LEVELS_HPA,
    build_route_profile_data,
    great_circle_point,
    haversine_km,
    normalize_eta_lead,
    route_waypoint_specs,
)


class RouteProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.origin = GeoPoint(55.7558, 37.6173, "Москва", "test")
        self.destination = GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "test")
        self.run = GfsRun("20260710", "00")

    def test_haversine_and_great_circle(self) -> None:
        distance = haversine_km(self.origin.lat, self.origin.lon, self.destination.lat, self.destination.lon)
        self.assertGreater(distance, 600)
        self.assertLess(distance, 700)
        lat, lon = great_circle_point(self.origin.lat, self.origin.lon, self.destination.lat, self.destination.lon, 0.5)
        self.assertGreater(lat, self.origin.lat)
        self.assertLess(lon, self.origin.lon)

    def test_waypoints_follow_speed_and_eta(self) -> None:
        distance, duration, specs = route_waypoint_specs(self.origin, self.destination, 24, speed_kmh=300)
        self.assertAlmostEqual(duration, distance / 300.0, places=6)
        self.assertGreaterEqual(len(specs), 3)
        self.assertEqual(specs[0][4], 24)
        self.assertGreaterEqual(specs[-1][4], 26)

    def test_lead_normalization_respects_gfs_schedule(self) -> None:
        self.assertEqual(normalize_eta_lead(24.4), 24)
        self.assertEqual(normalize_eta_lead(24.6), 25)
        self.assertEqual(normalize_eta_lead(121.0), 120)
        self.assertEqual(normalize_eta_lead(122.0), 123)

    @patch("route_profile._read_cloudgram_cell")
    @patch("route_profile.build_profile")
    def test_build_route_profile_marks_icing_and_high_risk(self, build_profile, read_surface) -> None:
        levels = np.asarray(ROUTE_LEVELS_HPA, dtype=float)
        heights = np.linspace(100, 5600, len(levels))
        temperatures = np.linspace(2, -22, len(levels))
        humidity = np.full(len(levels), 96.0)
        u = np.linspace(2, 45, len(levels))
        v = np.linspace(1, -30, len(levels))
        frame = pd.DataFrame({
            "pressure_hpa": levels,
            "temperature_k": temperatures + 273.15,
            "temperature_c": temperatures,
            "relative_humidity_pct": humidity,
            "u_wind_ms": u,
            "v_wind_ms": v,
            "wind_speed_ms": np.hypot(u, v),
            "wind_dir_deg": np.zeros(len(levels)),
            "geopotential_height_m": heights,
        })

        def profile_result(run, lead, lat, lon, progress_callback=None):
            return ProfileResult(run, lead, lat, lon, lat, lon, __import__("pathlib").Path("fake.grib2"), frame)

        build_profile.side_effect = profile_result
        surface = CloudgramCell(
            lead_hour=24,
            valid_time_utc=datetime(2026, 7, 11, tzinfo=timezone.utc),
            high_cloud_pct=80,
            mid_cloud_pct=90,
            low_cloud_pct=95,
            total_cloud_pct=100,
            ceiling_m=250,
            precip_mm=5,
            precip_rate_mmh=3,
            conv_precip_mm=1,
            precip_type="R",
            cape_jkg=900,
            cin_jkg=-20,
            cb_score=3,
            visibility_km=2,
            phenomena="TSRA",
            hazard_score=4,
            hazard_text="гроза",
        )
        read_surface.return_value = (surface, 55.75, 37.5, set())
        data = build_route_profile_data(self.run, self.origin, self.destination, 24, speed_kmh=300, mode="simple", max_points=4)
        self.assertEqual(data.temperature_c.shape[0], len(ROUTE_LEVELS_HPA))
        self.assertEqual(data.max_risk, 3)
        self.assertGreater(int(np.nanmax(data.icing_score)), 0)
        self.assertGreater(int(np.nanmax(data.turbulence_score)), 0)


if __name__ == "__main__":
    unittest.main()
