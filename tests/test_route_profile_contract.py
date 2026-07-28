from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

import numpy as np

import route_profile_contract as contract
import route_profile_plot as plot
import route_profile_vertical_policy as vertical_policy
from geocode import GeoPoint


class RouteProfileContractTests(unittest.TestCase):
    def setUp(self) -> None:
        vertical_policy.install()
        self.moscow = GeoPoint(55.7558, 37.6173, "Москва", "test")
        self.novosibirsk = GeoPoint(55.0084, 82.9357, "Новосибирск", "test")

    @staticmethod
    def _surface(**overrides):
        values = {
            "phenomena": "—",
            "cb_score": 0,
            "precip_mm": 0.0,
            "visibility_km": 20.0,
            "ceiling_m": 5000.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

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

    def test_weak_instability_without_weather_is_not_route_risk(self) -> None:
        surface = self._surface(cb_score=1, phenomena="—")
        self.assertFalse(contract.confirmed_thunder(surface))
        self.assertEqual(contract.surface_risk(surface), 0)

    def test_cb_score_two_is_not_thunderstorm_or_high_risk(self) -> None:
        surface = self._surface(cb_score=2, phenomena="—")
        self.assertFalse(contract.confirmed_thunder(surface))
        self.assertEqual(contract.surface_risk(surface), 2)

    def test_only_tsra_is_confirmed_thunderstorm(self) -> None:
        surface = self._surface(cb_score=3, phenomena="TSRA", precip_mm=1.0)
        self.assertTrue(contract.confirmed_thunder(surface))
        self.assertEqual(contract.surface_risk(surface), 3)

    def test_vertical_risk_is_computed_per_route_point(self) -> None:
        data = SimpleNamespace(
            icing_score=np.asarray(
                [
                    [0, 2, 0, 0],
                    [0, 2, 3, 0],
                    [0, 0, 3, 0],
                ],
                dtype=int,
            ),
            turbulence_score=np.asarray(
                [
                    [0, 0, 0, 3],
                    [0, 0, 0, 1],
                    [0, 0, 0, 0],
                ],
                dtype=int,
            ),
            wind_speed_ms=np.asarray(
                [
                    [10.0, 12.0, 15.0, 15.0],
                    [11.0, 14.0, 16.0, 16.0],
                    [12.0, 15.0, 17.0, 17.0],
                ]
            ),
        )
        self.assertEqual(contract.vertical_risk_for_point(data, 0), 0)
        self.assertEqual(contract.vertical_risk_for_point(data, 1), 2)
        self.assertEqual(contract.vertical_risk_for_point(data, 2), 2)
        self.assertEqual(contract.vertical_risk_for_point(data, 3), 2)

    def test_persistent_severe_layers_are_required_for_vertical_r3(self) -> None:
        data = SimpleNamespace(
            icing_score=np.asarray([[0], [3], [3], [0]], dtype=int),
            turbulence_score=np.asarray([[0], [0], [0], [0]], dtype=int),
            wind_speed_ms=np.asarray([[10.0], [10.0], [10.0], [10.0]], dtype=float),
        )
        self.assertEqual(contract.vertical_risk_for_point(data, 0), 3)

    def test_vertical_policy_is_bound_to_contract(self) -> None:
        self.assertIs(contract.vertical_risk_for_point, vertical_policy.vertical_risk_for_point)
        self.assertIs(contract.surface_risk, vertical_policy.surface_risk)

    def test_hazard_tokens_do_not_show_thunder_for_convective_potential(self) -> None:
        surface = self._surface(cb_score=2, phenomena="—")
        point = SimpleNamespace(surface=surface)
        data = SimpleNamespace(
            waypoints=(point,),
            icing_score=np.zeros((2, 1), dtype=int),
            turbulence_score=np.zeros((2, 1), dtype=int),
            wind_speed_ms=np.zeros((2, 1), dtype=float),
            cloud_mask=np.zeros((2, 1), dtype=bool),
        )
        keys = {token.key for token in plot._hazard_tokens_for_indices(data, (0,), limit=4)}
        self.assertNotIn("thunder", keys)

    def test_risk_signature_does_not_include_presentation_mode(self) -> None:
        point_risk = np.asarray([0, 1, 3], dtype=int)
        icing = np.asarray([[0, 1, 2]], dtype=int)
        turbulence = np.asarray([[1, 0, 3]], dtype=int)
        cloud = np.asarray([[False, True, True]], dtype=bool)
        surfaces = (
            SimpleNamespace(phenomena="—", cb_score=0),
            SimpleNamespace(phenomena="RA", cb_score=1),
            SimpleNamespace(phenomena="TSRA", cb_score=3),
        )
        waypoints = tuple(SimpleNamespace(surface=surface) for surface in surfaces)
        simple = SimpleNamespace(
            mode="simple",
            point_risk=point_risk,
            icing_score=icing,
            turbulence_score=turbulence,
            cloud_mask=cloud,
            waypoints=waypoints,
        )
        professional = SimpleNamespace(
            mode="pro",
            point_risk=point_risk.copy(),
            icing_score=icing.copy(),
            turbulence_score=turbulence.copy(),
            cloud_mask=cloud.copy(),
            waypoints=tuple(SimpleNamespace(surface=surface) for surface in surfaces),
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
