from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from route_profile_card_policy import assess_group, route_card_groups
from route_profile_plot import RouteDisplayGroup


class RouteProfileCardPolicyTests(unittest.TestCase):
    @staticmethod
    def _point(score: int, *, phenomena: str = "—", precip: float = 0.0):
        return SimpleNamespace(
            risk_score=score,
            distance_km=0.0,
            lead_hour=6,
            valid_time_utc=datetime(2026, 7, 12, 18, tzinfo=timezone.utc),
            surface=SimpleNamespace(
                phenomena=phenomena,
                visibility_km=20.0,
                ceiling_m=5000.0,
                precip_mm=precip,
            ),
        )

    @staticmethod
    def _data(scores: list[int]):
        count = len(scores)
        points = []
        for index, score in enumerate(scores):
            point = RouteProfileCardPolicyTests._point(score)
            point.distance_km = index * 25.0
            points.append(point)
        shape = (5, count)
        return SimpleNamespace(
            total_distance_km=max(25.0, (count - 1) * 25.0),
            waypoints=tuple(points),
            icing_score=np.zeros(shape, dtype=int),
            turbulence_score=np.zeros(shape, dtype=int),
            wind_speed_ms=np.full(shape, 10.0),
            cloud_mask=np.zeros(shape, dtype=bool),
        )

    @staticmethod
    def _group(count: int) -> RouteDisplayGroup:
        return RouteDisplayGroup(
            start_index=0,
            end_index=count - 1,
            start_km=0.0,
            end_km=max(25.0, (count - 1) * 25.0),
            center_km=max(12.5, (count - 1) * 12.5),
            point_indices=tuple(range(count)),
        )

    def test_isolated_high_point_does_not_paint_whole_card_red(self) -> None:
        data = self._data([0, 0, 3, 0, 0])
        assessment = assess_group(data, self._group(5))
        self.assertEqual(assessment.score, 2)
        self.assertEqual(assessment.peak_score, 3)

    def test_spatially_persistent_high_points_keep_high_risk(self) -> None:
        data = self._data([0, 3, 3, 0, 0])
        assessment = assess_group(data, self._group(5))
        self.assertEqual(assessment.score, 3)

    def test_short_route_has_fewer_than_twelve_cards(self) -> None:
        data = self._data([0] * 27)  # about 650 km at 25 km spacing
        groups = route_card_groups(data)
        self.assertGreaterEqual(len(groups), 5)
        self.assertLessEqual(len(groups), 7)

    def test_cloud_is_context_not_warning_when_hazard_exists(self) -> None:
        data = self._data([1, 1, 1, 1])
        data.cloud_mask[:, :] = True
        data.turbulence_score[2:4, :] = 2
        assessment = assess_group(data, self._group(4))
        keys = [hazard.key for hazard in assessment.hazards]
        self.assertIn("turbulence", keys)
        self.assertNotIn("cloud", keys)


if __name__ == "__main__":
    unittest.main()
