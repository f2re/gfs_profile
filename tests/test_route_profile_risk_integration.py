from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

import route_profile_contract as contract
import route_profile_vertical_policy  # noqa: F401
from geocode import GeoPoint
from gfs_core import GfsRun
from route_profile import RouteProfileData, RouteWaypoint


class RouteProfileRiskIntegrationTests(unittest.TestCase):
    @staticmethod
    def _surface(*, cb_score: int, phenomena: str):
        return SimpleNamespace(
            cb_score=cb_score,
            phenomena=phenomena,
            precip_mm=1.0 if phenomena == "TSRA" else 0.0,
            visibility_km=20.0,
            ceiling_m=5000.0,
        )

    def test_recompute_risk_per_point_does_not_spread_thunder(self) -> None:
        run = GfsRun("20260710", "06")
        origin = GeoPoint(55.75, 37.62, "Москва", "test")
        destination = GeoPoint(59.94, 30.31, "Санкт-Петербург", "test")
        surfaces = (
            self._surface(cb_score=1, phenomena="—"),
            self._surface(cb_score=2, phenomena="—"),
            self._surface(cb_score=3, phenomena="TSRA"),
        )
        waypoints = tuple(
            RouteWaypoint(
                index=index,
                fraction=index / 2.0,
                lat=55.0 + index,
                lon=37.0 - index,
                distance_km=float(index * 100),
                elapsed_hours=float(index),
                lead_hour=24 + index,
                valid_time_utc=datetime(2026, 7, 11, 6 + index, tzinfo=timezone.utc),
                grid_lat=55.0 + index,
                grid_lon=37.0 - index,
                profile=pd.DataFrame(),
                surface=surface,
                risk_score=3,
                risk_reasons=("старое значение",),
            )
            for index, surface in enumerate(surfaces)
        )
        levels = (1000, 900, 800)
        shape = (len(levels), len(waypoints))
        icing = np.asarray(
            [
                [0, 2, 0],
                [0, 2, 0],
                [0, 0, 0],
            ],
            dtype=int,
        )
        data = RouteProfileData(
            run=run,
            origin=origin,
            destination=destination,
            departure_lead=24,
            speed_kmh=300,
            mode="simple",
            total_distance_km=200.0,
            duration_hours=2.0 / 3.0,
            levels_hpa=levels,
            waypoints=waypoints,
            temperature_c=np.zeros(shape),
            humidity_pct=np.zeros(shape),
            wind_speed_ms=np.full(shape, 10.0),
            wind_dir_deg=np.zeros(shape),
            u_wind_ms=np.zeros(shape),
            v_wind_ms=np.zeros(shape),
            height_m=np.zeros(shape),
            icing_score=icing,
            turbulence_score=np.zeros(shape, dtype=int),
            cloud_mask=np.zeros(shape, dtype=bool),
            point_risk=np.full(len(waypoints), 3, dtype=int),
        )

        result = contract.recompute_objective_risk(data)

        self.assertEqual(result.point_risk.tolist(), [0, 2, 3])
        self.assertNotIn("гроза", result.waypoints[0].risk_reasons)
        self.assertNotIn("гроза", result.waypoints[1].risk_reasons)
        self.assertIn("конвективный риск", result.waypoints[1].risk_reasons)
        self.assertIn("гроза", result.waypoints[2].risk_reasons)


if __name__ == "__main__":
    unittest.main()
