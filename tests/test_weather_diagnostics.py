from __future__ import annotations

import unittest

from weather_diagnostics import DASH, precipitation_code, thunder_score, visibility_km, weather_code


class WeatherDiagnosticsTests(unittest.TestCase):
    def test_visibility_conversion(self) -> None:
        self.assertEqual(visibility_km(8000.0), 8.0)
        self.assertEqual(visibility_km(8.0), 8.0)
        self.assertIsNone(visibility_km(None))

    def test_thunder_score_is_capped(self) -> None:
        self.assertEqual(thunder_score(1500.0, -20.0, 1.0, 80.0, 5.0), 3)
        self.assertEqual(thunder_score(100.0, -300.0, 0.0, 0.0, 0.0), 0)

    def test_weather_codes(self) -> None:
        self.assertEqual(precipitation_code(True, False, False, False), "R")
        self.assertEqual(precipitation_code(False, True, False, False), "S")
        self.assertEqual(weather_code(1.0, "R", 2, 8.0), "TSRA")
        self.assertEqual(weather_code(0.0, DASH, 0, 0.5), "FG")
        self.assertEqual(weather_code(1.0, "FZ", 0, 8.0), "FZRA")
        self.assertEqual(weather_code(1.0, "S", 0, 8.0), "SN")
        self.assertEqual(weather_code(1.0, "R", 0, 8.0), "RA")


if __name__ == "__main__":
    unittest.main()
