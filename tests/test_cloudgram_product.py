from __future__ import annotations

import unittest

from gfs_core import GfsProfileError
from cloudgram_product import cloudgram_leads, _cb_score


class CloudgramProductTests(unittest.TestCase):
    def test_default_leads_are_three_hourly_to_72(self) -> None:
        self.assertEqual(cloudgram_leads(0, 12, 3), [0, 3, 6, 9, 12])

    def test_cloudgram_is_limited_to_120_hours(self) -> None:
        with self.assertRaises(GfsProfileError):
            cloudgram_leads(0, 123, 3)

    def test_cb_score_is_capped_to_three(self) -> None:
        self.assertEqual(_cb_score(1500.0, -20.0, 2.0, 80.0, 10.0), 3)

    def test_cb_score_without_signals_is_zero(self) -> None:
        self.assertEqual(_cb_score(None, None, None, None, None), 0)


if __name__ == "__main__":
    unittest.main()
