from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import admin_stats


class AdminStatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "admin.sqlite3"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_user_search_and_admin_add(self) -> None:
        user = SimpleNamespace(id=42, username="meteo_admin", first_name="Meteo", last_name="Admin", is_bot=False)
        admin_stats.record_telegram_user(user, db_path=self.db_path)

        users = admin_stats.find_users("@meteo", db_path=self.db_path)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].user_id, 42)

        self.assertFalse(admin_stats.is_admin(42, db_path=self.db_path))
        added = admin_stats.add_admin_by_query("@meteo_admin", added_by=1, db_path=self.db_path)
        self.assertEqual(added.user_id, 42)
        self.assertTrue(admin_stats.is_admin(42, db_path=self.db_path))

    def test_request_summary_and_csv_export(self) -> None:
        user = SimpleNamespace(id=7, username="forecaster", first_name="F", last_name=None, is_bot=False)
        admin_stats.record_telegram_user(user, db_path=self.db_path)
        request_id = admin_stats.record_request_start(
            product="profile",
            user_id=7,
            username="forecaster",
            city="Москва",
            request_text="/profile Москва +24",
            lead_from=24,
            lead_to=24,
            db_path=self.db_path,
        )
        admin_stats.record_request_finish(request_id, status="ok", duration_ms=1234, db_path=self.db_path)

        summary = admin_stats.usage_summary(days=1, db_path=self.db_path)
        self.assertEqual(summary["total_requests"], 1)
        self.assertEqual(summary["active_users"], 1)
        self.assertEqual(summary["products"][0][0], "profile")
        self.assertIn("Москва", admin_stats.export_requests_csv(days=1, db_path=self.db_path))
        self.assertIn("forecaster", admin_stats.export_users_csv(db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
