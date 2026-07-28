from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

import route_profile_contract as contract
import route_profile_plot as plot
import route_profile_vertical_policy  # noqa: F401


class RouteProfileCardRiskTests(unittest.TestCase):
    @staticmethod
    def _surface(*, phenomena: str = "—", cb_score: int = 0):
        return SimpleNamespace(
            phenomena=phenomena,
            cb_score=cb_score,
            precip_mm=1.0 if phenomena == "TSRA" else 0.0,
            visibility_km=20.0,
            ceiling_m=5000.0,
        )

    def test_thunder_token_is_local_to_selected_points(self) -> None:
        points = tuple(
            SimpleNamespace(surface=self._surface(phenomena="TSRA", cb_score=3) if index == 5 else self._surface())
            for index in range(10)
        )
        data = SimpleNamespace(
            waypoints=points,
            icing_score=np.zeros((3, 10), dtype=int),
            turbulence_score=np.zeros((3, 10), dtype=int),
            wind_speed_ms=np.zeros((3, 10), dtype=float),
            cloud_mask=np.zeros((3, 10), dtype=bool),
        )

        before = {token.key for token in plot._hazard_tokens_for_indices(data, range(0, 5), limit=4)}
        containing = {token.key for token in plot._hazard_tokens_for_indices(data, range(5, 7), limit=4)}
        after = {token.key for token in plot._hazard_tokens_for_indices(data, range(7, 10), limit=4)}

        self.assertNotIn("thunder", before)
        self.assertIn("thunder", containing)
        self.assertNotIn("thunder", after)

    def test_convective_potential_does_not_become_thunder_token(self) -> None:
        data = SimpleNamespace(
            waypoints=(SimpleNamespace(surface=self._surface(cb_score=2)),),
            icing_score=np.zeros((2, 1), dtype=int),
            turbulence_score=np.zeros((2, 1), dtype=int),
            wind_speed_ms=np.zeros((2, 1), dtype=float),
            cloud_mask=np.zeros((2, 1), dtype=bool),
        )
        keys = {token.key for token in plot._hazard_tokens_for_indices(data, (0,), limit=4)}
        self.assertNotIn("thunder", keys)
        self.assertFalse(contract.confirmed_thunder(data.waypoints[0].surface))


if __name__ == "__main__":
    unittest.main()
