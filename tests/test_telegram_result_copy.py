from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np


class TelegramResultCopyTests(unittest.TestCase):
    def test_aero_caption_keeps_gfs_without_repeated_disclaimer(self) -> None:
        import telegram_aero

        result = SimpleNamespace(
            run=SimpleNamespace(date="20260712", cycle="12"),
            lead_hour=24,
            valid_time_utc=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
            grid_lat=55.75,
            grid_lon=37.62,
        )
        text = telegram_aero.format_aero_caption(result)
        self.assertIn("GFS", text)
        self.assertNotIn("не радиозонд", text.lower())

    def test_map_status_keeps_gfs_without_radar_phrase(self) -> None:
        import telegram_map

        data = {
            "run": SimpleNamespace(date="20260712", cycle="12"),
            "point": SimpleNamespace(label="Москва"),
            "lead_hour": 24,
        }
        text = telegram_map.format_map_status(data)
        self.assertIn("GFS", text)
        self.assertNotIn("не радар", text.lower())
        self.assertNotIn("не наблю", text.lower())

    def test_route_summary_keeps_risk_and_spatial_step(self) -> None:
        import telegram_bot  # Final application composition must not strip source summaries.
        import telegram_route
        point = SimpleNamespace(risk_score=1, risk_reasons=["обледенение"], distance_km=0)
        data = SimpleNamespace(
            point_risk=np.array([1]), max_risk=1, waypoints=[point], mode="simple",
            origin=SimpleNamespace(label="Москва"), destination=SimpleNamespace(label="Тула"),
            run=SimpleNamespace(date="20260712", cycle="12"), departure_lead=24, arrival_lead=25,
            total_distance_km=170, duration_hours=0.6, speed_kmh=300,
        )
        text = telegram_route.route_summary(data)
        self.assertIn("GFS data", text)
        self.assertIn("обледенение", text)
        self.assertIn("пространственный шаг", text)
        self.assertNotIn("не радиозонд", text)


if __name__ == "__main__":
    unittest.main()
