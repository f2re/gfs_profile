from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np

import route_profile_contract as contract
import route_profile_plot as plot
import route_profile_vertical_policy  # noqa: F401


class RouteProfileNoFalseThunderTests(unittest.TestCase):
    def test_cloudy_route_without_tsra_has_no_thunder_tokens(self) -> None:
        points = tuple(
            SimpleNamespace(
                surface=SimpleNamespace(
                    phenomena="—",
                    cb_score=1,
                    precip_mm=0.0,
                    visibility_km=20.0,
                    ceiling_m=3000.0,
                )
            )
            for _ in range(12)
        )
        data = SimpleNamespace(
            waypoints=points,
            icing_score=np.zeros((5, 12), dtype=int),
            turbulence_score=np.zeros((5, 12), dtype=int),
            wind_speed_ms=np.full((5, 12), 10.0),
            cloud_mask=np.ones((5, 12), dtype=bool),
        )

        for start in range(0, 12, 3):
            keys = {
                token.key
                for token in plot._hazard_tokens_for_indices(
                    data,
                    range(start, min(start + 3, 12)),
                    limit=4,
                )
            }
            self.assertNotIn("thunder", keys)

        self.assertTrue(all(contract.surface_risk(point.surface) == 0 for point in points))


if __name__ == "__main__":
    unittest.main()
