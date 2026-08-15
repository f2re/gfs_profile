from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telegram_schedules import (
    MAX_SCHEDULES_PER_USER,
    ProductSchedule,
    ScheduleLimitError,
    ScheduleStore,
    _manager_text,
    next_run_utc,
    schedule_spec_from_meteogram_state,
    schedule_spec_from_product_state,
)


class ScheduleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "schedules.json"
        self.store = ScheduleStore(self.path)
        self.point = {"lat": 44.0393, "lon": 43.0708, "label": "Пятигорск", "source": "test"}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add(self, suffix: int = 0, **kwargs):
        return self.store.add(
            user_id=100,
            chat_id=100,
            username="meteo",
            product=kwargs.pop("product", "map"),
            point=kwargs.pop("point", self.point),
            params=kwargs.pop("params", {"mode": "gif", "from": 0, "to": 24 + suffix, "time_step": 3}),
            timezone_name=kwargs.pop("timezone_name", "Europe/Moscow"),
            local_time=kwargs.pop("local_time", "06:00"),
            every_days=kwargs.pop("every_days", 3),
            now_utc=kwargs.pop("now_utc", datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)),
            **kwargs,
        )

    def test_limit_two_per_user_and_atomic_roundtrip(self) -> None:
        first = self._add(0)
        second = self._add(3)
        self.assertEqual(MAX_SCHEDULES_PER_USER, 2)
        self.assertEqual([item.schedule_id for item in self.store.list_for_user(100)], [first.schedule_id, second.schedule_id])
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["schedules"]), 2)
        with self.assertRaises(ScheduleLimitError):
            self._add(6)
        # Another user has an independent two-schedule allowance.
        other = self.store.add(
            user_id=200,
            chat_id=200,
            username=None,
            product="profile",
            point=self.point,
            params={"lead": 24},
            timezone_name="Europe/Moscow",
            local_time="13:00",
            every_days=1,
            now_utc=datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(other.user_id, 200)

    def test_delete_requires_owner(self) -> None:
        item = self._add()
        self.assertFalse(self.store.delete(item.schedule_id, 999))
        self.assertIsNotNone(self.store.get(item.schedule_id))
        self.assertTrue(self.store.delete(item.schedule_id, 100))
        self.assertIsNone(self.store.get(item.schedule_id))

    def test_claim_due_preadvances_and_skips_stale(self) -> None:
        item = self._add(
            every_days=1,
            local_time="13:00",
            now_utc=datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc),
        )
        due_at = item.next_run_datetime_utc
        due, skipped = self.store.claim_due(due_at, max_late_minutes=180)
        self.assertEqual([row.schedule_id for row in due], [item.schedule_id])
        self.assertFalse(skipped)
        refreshed = self.store.get(item.schedule_id)
        self.assertIsNotNone(refreshed)
        self.assertGreater(refreshed.next_run_datetime_utc, due_at)

        stale_store = ScheduleStore(Path(self.temp.name) / "stale.json")
        stale = stale_store.add(
            user_id=300,
            chat_id=300,
            username=None,
            product="profile",
            point=self.point,
            params={"lead": 24},
            timezone_name="UTC",
            local_time="06:00",
            every_days=1,
            now_utc=datetime(2026, 8, 15, 5, 0, tzinfo=timezone.utc),
        )
        due, skipped = stale_store.claim_due(
            datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
            max_late_minutes=180,
        )
        self.assertFalse(due)
        self.assertEqual([row.schedule_id for row in skipped], [stale.schedule_id])
        self.assertGreater(
            stale_store.get(stale.schedule_id).next_run_datetime_utc,
            datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
        )

    def test_manager_shows_local_schedule_and_capacity(self) -> None:
        self._add()
        text, rows = _manager_text(100, self.store)
        self.assertIn("1/2", text)
        self.assertIn("Пятигорск", text)
        self.assertIn("06:00 Europe/Moscow", text)
        self.assertIn("каждые 3 дня", text)
        self.assertEqual(len(rows), 1)


class ScheduleTimeTests(unittest.TestCase):
    def test_first_run_is_next_local_clock_time(self) -> None:
        # 10:00 UTC = 13:00 Moscow, so 06:00 should be tomorrow.
        value = next_run_utc(
            "Europe/Moscow",
            "06:00",
            3,
            now_utc=datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(value, datetime(2026, 8, 16, 3, 0, tzinfo=timezone.utc))
        # The following recurrence is three local calendar days later.
        value2 = next_run_utc(
            "Europe/Moscow",
            "06:00",
            3,
            previous_scheduled_utc=value,
        )
        self.assertEqual(value2, datetime(2026, 8, 19, 3, 0, tzinfo=timezone.utc))

    def test_calendar_time_survives_dst_transition(self) -> None:
        before = datetime(2026, 3, 28, 6, 0, tzinfo=timezone.utc)  # 06:00 London GMT
        after = next_run_utc(
            "Europe/London",
            "06:00",
            1,
            previous_scheduled_utc=before,
        )
        # 29 March is BST, so 06:00 local is 05:00 UTC.
        self.assertEqual(after, datetime(2026, 3, 29, 5, 0, tzinfo=timezone.utc))


class ScheduleSpecTests(unittest.TestCase):
    def test_map_animation_preserves_all_parameters(self) -> None:
        state = {
            "product": "map",
            "point": {"lat": 44.0393, "lon": 43.0708, "label": "Пятигорск", "source": "test"},
            "mode": "gif",
            "from": 0,
            "to": 48,
            "time_step": 3,
            "basemap": "places",
            "radius": 80,
        }
        spec = schedule_spec_from_product_state(state)
        self.assertEqual(spec["product"], "map")
        self.assertEqual(spec["params"]["mode"], "gif")
        self.assertEqual(spec["params"]["to"], 48)
        self.assertEqual(spec["params"]["time_step"], 3)
        self.assertEqual(spec["params"]["radius"], 80.0)

    def test_document_meteogram_preserves_model_period_and_format(self) -> None:
        state = {
            "point": {"lat": 68.9707, "lon": 33.0749, "label": "Мурманск", "source": "test"},
            "source_id": "gefs",
            "days": 5,
            "output_format": "pdf",
        }
        spec = schedule_spec_from_meteogram_state(state)
        self.assertEqual(spec["product"], "meteogram")
        self.assertEqual(spec["params"], {"source_id": "gefs", "days": 5, "output_format": "pdf"})


if __name__ == "__main__":
    unittest.main()
