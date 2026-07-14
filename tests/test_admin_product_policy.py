from __future__ import annotations

import unittest
from unittest.mock import patch

import admin_product_policy
import admin_stats


class AdminProductPolicyTests(unittest.TestCase):
    def test_historical_skewt_is_merged_into_aero(self) -> None:
        original = admin_stats.usage_summary
        admin_stats._AERO_ALIAS_INSTALLED = False
        with patch.object(
            admin_stats,
            "usage_summary",
            return_value={
                "days": 7,
                "total_users": 1,
                "active_users": 1,
                "total_requests": 5,
                "failed_requests": 0,
                "avg_duration_ms": 1000,
                "products": [("aero", 2, 1000), ("skewt", 3, 2000)],
                "cities": [],
            },
        ):
            namespace = {"format_recent_requests": lambda limit=10, db_path=None: "skewt"}
            admin_product_policy.install(namespace)
            data = admin_stats.usage_summary(7)
            self.assertEqual(data["products"], [("aero", 5, 1600)])
            self.assertNotIn("skewt", namespace["format_recent_requests"]())
        admin_stats.usage_summary = original
        admin_stats._AERO_ALIAS_INSTALLED = False


if __name__ == "__main__":
    unittest.main()
