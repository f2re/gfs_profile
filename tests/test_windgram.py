from __future__ import annotations

import unittest

from windgram_product import windgram_leads


class WindgramTests(unittest.TestCase):
    def test_leads_with_six_hour_step(self) -> None:
        self.assertEqual(windgram_leads(0, 24, 6), [0, 6, 12, 18, 24])

    def test_leads_skip_non_gfs_hours_after_120(self) -> None:
        self.assertEqual(windgram_leads(120, 132, 6), [120, 126, 132])


if __name__ == "__main__":
    unittest.main()
