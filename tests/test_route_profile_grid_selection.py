from __future__ import annotations

import inspect
import unittest

import numpy as np

from geocode import GeoPoint
from gfs_core import GfsProfileError
from route_profile_contract import build_route_profile_data, route_waypoint_specs, validate_spatial_step


class RouteProfileGridSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.origin = GeoPoint(55.7558, 37.6173, "Москва", "test")
        self.destination = GeoPoint(55.0084, 82.9357, "Новосибирск", "test")

    def test_user_grid_steps_change_point_count(self) -> None:
        plans = {
            step: route_waypoint_specs(
                self.origin,
                self.destination,
                6,
                speed_kmh=300,
                spatial_step_km=step,
            )[2]
            for step in (25, 50, 100)
        }
        self.assertGreater(len(plans[25]), len(plans[50]))
        self.assertGreater(len(plans[50]), len(plans[100]))
        for step, specs in plans.items():
            distances = np.asarray([item[3] for item in specs], dtype=float)
            self.assertLessEqual(float(np.max(np.diff(distances))), step + 0.01)
            self.assertAlmostEqual(distances[0], 0.0)
            self.assertAlmostEqual(distances[-1], plans[25][-1][3])

    def test_only_supported_grid_steps_are_accepted(self) -> None:
        self.assertEqual(validate_spatial_step(25), 25)
        self.assertEqual(validate_spatial_step(50), 50)
        self.assertEqual(validate_spatial_step(100), 100)
        with self.assertRaises(GfsProfileError):
            validate_spatial_step(75)

    def test_builder_accepts_spatial_step(self) -> None:
        parameters = inspect.signature(build_route_profile_data).parameters
        self.assertIn("spatial_step_km", parameters)
        self.assertEqual(parameters["spatial_step_km"].default, 25)


if __name__ == "__main__":
    unittest.main()
