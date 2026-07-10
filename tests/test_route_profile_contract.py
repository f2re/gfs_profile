from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

import numpy as np

import route_profile_contract as contract
import route_profile_plot as plot
from geocode import GeoPoint


class RouteProfileContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.moscow = GeoPoint(55.7558, 37.6173, "Москва", "test")
        self.novosibirsk = GeoPoint(55.0084, 82.9357, "Новосибирск", "test")

    def test_long_route_uses_about_25_km_spatial_step(self) -> None:
        distance, duration, specs = contract.route_waypoint_specs(
            self.moscow,
            self.novosibirsk,
            6,
            speed_kmh=300,
        )
        self.assertGreater(distance, 2700)
        self.assertAlmostEqual(duration, distance / 300.0, places=6)
        self.assertGreater(len(specs), 100)
        self.assertLessEqual(len(specs), contract.ROUTE_MAX_POINTS)
        spacings = np.diff([item[3] for item in specs])
        self.assertLessEqual(float(np.max(spacings)), contract.ROUTE_SPATIAL_STEP_KM + 0.01)

    def test_speed_changes_eta_not_spatial_points(self) -> None:
        _, _, slow = contract.route_waypoint_specs(self.moscow, self.novosibirsk, 6, speed_kmh=300)
        _, _, fast = contract.route_waypoint_specs(self.moscow, self.novosibirsk, 6, speed_kmh=600)
        np.testing.assert_allclose([item[3] for item in slow], [item[3] for item in fast])
        self.assertGreaterEqual(slow[-1][4], fast[-1][4])

    def test_risk_signature_does_not_include_presentation_mode(self) -> None:
        point_risk = np.asarray([0, 1, 3], dtype=int)
        icing = np.asarray([[0, 1, 2]], dtype=int)
        turbulence = np.asarray([[1, 0, 3]], dtype=int)
        cloud = np.asarray([[False, True, True]], dtype=bool)
        simple = SimpleNamespace(
            mode="simple",
            point_risk=point_risk,
            icing_score=icing,
            turbulence_score=turbulence,
            cloud_mask=cloud,
        )
        professional = SimpleNamespace(
            mode="pro",
            point_risk=point_risk.copy(),
            icing_score=icing.copy(),
            turbulence_score=turbulence.copy(),
            cloud_mask=cloud.copy(),
        )
        self.assertEqual(contract.risk_signature(simple), contract.risk_signature(professional))

    def test_simple_and_pro_use_same_risk_card_boundaries(self) -> None:
        self.assertEqual(plot._MAX_SIMPLE_CARDS, plot._MAX_PRO_CARDS)
        self.assertEqual(plot._MAX_SIMPLE_CARDS, contract.ROUTE_RISK_CARD_LIMIT)

    def test_build_wrapper_uses_new_operational_limit(self) -> None:
        default = inspect.signature(contract.build_route_profile_data).parameters["max_points"].default
        self.assertEqual(default, contract.ROUTE_MAX_POINTS)


if __name__ == "__main__":
    unittest.main()
