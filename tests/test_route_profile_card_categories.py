from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

import route_profile_contract as contract
import route_profile_vertical_policy as vertical_policy


class RouteProfileCardCategoryTests(unittest.TestCase):
    @staticmethod
    def _surface(cb_score: int = 0, phenomena: str = "—"):
        return SimpleNamespace(
            cb_score=cb_score,
            phenomena=phenomena,
            precip_mm=0.0,
            visibility_km=20.0,
            ceiling_m=5000.0,
        )

    def test_calm_columns_are_not_high_risk(self) -> None:
        data = SimpleNamespace(
            icing_score=np.zeros((6, 8), dtype=int),
            turbulence_score=np.zeros((6, 8), dtype=int),
            wind_speed_ms=np.full((6, 8), 12.0),
        )
        self.assertEqual([contract.vertical_risk_for_point(data, index) for index in range(8)], [0] * 8)

    def test_single_severe_level_is_not_high_risk(self) -> None:
        turbulence = np.zeros((6, 1), dtype=int)
        turbulence[2, 0] = 3
        data = SimpleNamespace(
            icing_score=np.zeros((6, 1), dtype=int),
            turbulence_score=turbulence,
            wind_speed_ms=np.full((6, 1), 12.0),
        )
        self.assertEqual(contract.vertical_risk_for_point(data, 0), 2)

    def test_two_turbulence_nodes_are_one_layer_not_high_risk(self) -> None:
        turbulence = np.zeros((6, 1), dtype=int)
        turbulence[2:4, 0] = 3
        data = SimpleNamespace(
            icing_score=np.zeros((6, 1), dtype=int),
            turbulence_score=turbulence,
            wind_speed_ms=np.full((6, 1), 12.0),
        )
        self.assertEqual(contract.vertical_risk_for_point(data, 0), 2)

    def test_three_consecutive_turbulence_nodes_are_high_risk(self) -> None:
        turbulence = np.zeros((6, 1), dtype=int)
        turbulence[1:4, 0] = 3
        data = SimpleNamespace(
            icing_score=np.zeros((6, 1), dtype=int),
            turbulence_score=turbulence,
            wind_speed_ms=np.full((6, 1), 12.0),
        )
        self.assertEqual(contract.vertical_risk_for_point(data, 0), 3)

    def test_two_adjacent_severe_icing_levels_are_high_risk(self) -> None:
        icing = np.zeros((6, 1), dtype=int)
        icing[2:4, 0] = 3
        data = SimpleNamespace(
            icing_score=icing,
            turbulence_score=np.zeros((6, 1), dtype=int),
            wind_speed_ms=np.full((6, 1), 12.0),
        )
        self.assertEqual(contract.vertical_risk_for_point(data, 0), 3)

    def test_shear_thresholds_are_conservative(self) -> None:
        self.assertEqual(vertical_policy.shear_severity(5.9), 0)
        self.assertEqual(vertical_policy.shear_severity(6.0), 1)
        self.assertEqual(vertical_policy.shear_severity(10.0), 2)
        self.assertEqual(vertical_policy.shear_severity(15.0), 3)

    def test_weak_instability_is_not_surface_risk(self) -> None:
        self.assertEqual(contract.surface_risk(self._surface(cb_score=1)), 0)


if __name__ == "__main__":
    unittest.main()
