from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from messenger.product_executor import ProductSnapshot
from messenger.schedule_store import MessengerScheduleStore, ScheduleError, ScheduleLimitError, next_run_utc


class MessengerScheduleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = MessengerScheduleStore(self.path)
        self.snapshot = ProductSnapshot.from_values(
            "profile",
            {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "test"},
            {"lead": 24, "run": "old"},
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_platform_scoping_and_limit(self) -> None:
        now = datetime(2026, 9, 6, 0, tzinfo=timezone.utc)
        first = self.store.add("max", "42", "chat", self.snapshot, "Europe/Moscow", "06:00", 1, now_utc=now)
        self.store.add("max", "42", "chat", self.snapshot, "Europe/Moscow", "07:00", 1, now_utc=now)
        with self.assertRaises(ScheduleLimitError):
            self.store.add("max", "42", "chat", self.snapshot, "Europe/Moscow", "08:00", 1, now_utc=now)
        vk = self.store.add("vk", "42", "chat", self.snapshot, "Europe/Moscow", "08:00", 1, now_utc=now)
        self.assertEqual(len(self.store.list_for_user("max", "42")), 2)
        self.assertEqual(len(self.store.list_for_user("vk", "42")), 1)
        self.assertNotEqual(first.schedule_id, vk.schedule_id)

    def test_duplicate_schedule_is_rejected(self) -> None:
        now = datetime(2026, 9, 6, 0, tzinfo=timezone.utc)
        self.store.add("max", "42", "chat", self.snapshot, "Europe/Moscow", "06:00", 1, now_utc=now)
        with self.assertRaises(ScheduleError):
            self.store.add("max", "42", "other-chat", self.snapshot, "Europe/Moscow", "06:00", 1, now_utc=now)

    def test_claim_advances_before_execution(self) -> None:
        now = datetime(2026, 9, 6, 2, 59, tzinfo=timezone.utc)
        item = self.store.add("max", "42", "chat", self.snapshot, "Europe/Moscow", "06:00", 1, now_utc=now)
        due_at = item.next_run_datetime_utc
        due, skipped = self.store.claim_due(now_utc=due_at)
        self.assertEqual([value.schedule_id for value in due], [item.schedule_id])
        self.assertEqual(skipped, [])
        refreshed = self.store.get("max", "42", item.schedule_id)
        self.assertGreater(refreshed.next_run_datetime_utc, due_at)

    def test_next_run_preserves_local_wall_clock(self) -> None:
        now = datetime(2026, 3, 28, 10, tzinfo=timezone.utc)
        first = next_run_utc("Europe/Berlin", "08:00", 1, now_utc=now)
        second = next_run_utc("Europe/Berlin", "08:00", 1, previous_scheduled_utc=first)
        self.assertEqual(first.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Berlin")).strftime("%H:%M"), "08:00")
        self.assertEqual(second.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Berlin")).strftime("%H:%M"), "08:00")


if __name__ == "__main__":
    unittest.main()
