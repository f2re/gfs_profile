from __future__ import annotations

import unittest

from cloudgram_product import _hazard_score, _phenomena, _visibility_km


class CloudgramHazardTests(unittest.TestCase):
    def test_visibility_units(self) -> None:
        self.assertEqual(_visibility_km(10000.0), 10.0)
        self.assertEqual(_visibility_km(8000.0), 8.0)
        self.assertEqual(_visibility_km(100.0), 0.1)
        self.assertIsNone(_visibility_km(None))

    def test_phenomena_codes(self) -> None:
        # score=2 is convective potential, not a confirmed thunderstorm.
        self.assertEqual(_phenomena(1.0, "R", 2, 8.0), "RA")
        self.assertEqual(_phenomena(1.0, "R", 3, 8.0), "TSRA")
        self.assertEqual(_phenomena(0.0, "—", 0, 0.5), "FG")
        self.assertEqual(_phenomena(1.0, "S", 0, 8.0), "SN")
        self.assertEqual(_phenomena(1.0, "FZ", 0, 8.0), "FZRA")
        self.assertEqual(_phenomena(0.0, "—", 0, 8.0), "—")

    def test_hazard_scale(self) -> None:
        score, text = _hazard_score(0, None, None, None, "—")
        self.assertEqual(score, 0)
        self.assertTrue(text)

        score, text = _hazard_score(0, 1.0, 1500.0, 10.0, "RA")
        self.assertEqual(score, 1)
        self.assertTrue(text)

        score, text = _hazard_score(0, None, 800.0, 10.0, "—")
        self.assertEqual(score, 2)
        self.assertTrue(text)

        score, text = _hazard_score(2, 0.5, 1500.0, 10.0, "RA")
        self.assertEqual(score, 2)
        self.assertIn("конвективный потенциал", text)
        self.assertNotIn("модельная гроза", text)

        score, text = _hazard_score(3, 0.5, 1500.0, 10.0, "TSRA")
        self.assertEqual(score, 4)
        self.assertIn("модельная гроза", text)


if __name__ == "__main__":
    unittest.main()
