from __future__ import annotations

import unittest
from types import SimpleNamespace

from geocode import GeoPoint
from product_progress import _progress_text as product_progress_text
from telegram_progress import _progress_text as profile_progress_text


class CompactProgressTests(unittest.TestCase):
    def test_product_progress_hides_implementation_details(self) -> None:
        text = product_progress_text("HEADER", {"stage": "parse_start"})
        self.assertIn("Читаю метеополя", text)
        self.assertNotIn("cfgrib", text)
        self.assertNotIn("eccodes", text)
        self.assertNotIn("NOMADS", text)

    def test_map_progress_is_short(self) -> None:
        text = product_progress_text("MAP", {"stage": "map_download", "downloaded": 50, "total": 100})
        self.assertIn("50%", text)
        self.assertLess(len(text), 100)

    def test_profile_progress_has_only_product_point_and_stage(self) -> None:
        run = SimpleNamespace(date="20260712", cycle="12")
        point = GeoPoint(55.75, 37.62, "Москва", "test")
        text = profile_progress_text(run, 24, point, {"stage": "parse_start"})
        self.assertIn("Москва", text)
        self.assertIn("+24 ч", text)
        self.assertIn("Читаю профиль", text)
        self.assertNotIn("forecast-файла", text)
        self.assertNotIn("cfgrib", text)


if __name__ == "__main__":
    unittest.main()
