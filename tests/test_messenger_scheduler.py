from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from messenger.contracts import PlatformMessage
from messenger.product_executor import ProductSnapshot
from messenger.schedule_store import MessengerScheduleStore, iso_utc
from messenger.scheduler import MessengerScheduler


class Gateway:
    platform = "max"
    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None): return PlatformMessage(self.platform, str(chat_id), "1")
    async def edit_text(self, *a, **k): return PlatformMessage(self.platform, str(a[0]), str(a[1]))
    async def send_image(self, chat_id, path, *, caption=""): return PlatformMessage(self.platform, str(chat_id), "i")
    async def send_file(self, chat_id, path, *, caption="", filename=None): return PlatformMessage(self.platform, str(chat_id), "f")
    async def send_animation(self, chat_id, path, *, caption=""): return PlatformMessage(self.platform, str(chat_id), "a")
    async def answer_callback(self, event, *, text=None): return None


class Executor:
    def __init__(self): self.calls = []
    async def execute(self, item, gateway):
        self.calls.append((item.platform, item.schedule_id, gateway.platform))
        return True


class MessengerSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = MessengerScheduleStore(self.path)
        self.snapshot = ProductSnapshot.from_values("profile", {"lat":55.75,"lon":37.62,"label":"Москва","source":"test"}, {"lead":24})

    async def asyncTearDown(self): self.tmp.cleanup()

    def _due(self, schedule_id: int) -> None:
        self.store.init()
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                conn.execute("UPDATE messenger_schedules SET next_run_utc=? WHERE id=?", (iso_utc(datetime.now(timezone.utc)-timedelta(minutes=1)), schedule_id))
        finally: conn.close()

    async def test_unavailable_vk_does_not_block_max_schedule(self):
        now = datetime.now(timezone.utc)
        max_item = self.store.add("max", "1", "chat1", self.snapshot, "Europe/Moscow", "06:00", 1, now_utc=now)
        vk_item = self.store.add("vk", "2", "chat2", self.snapshot, "Europe/Moscow", "06:00", 1, now_utc=now)
        self._due(max_item.schedule_id); self._due(vk_item.schedule_id)
        executor = Executor(); gateway = Gateway()
        scheduler = MessengerScheduler(store=self.store, executor=executor, gateways=lambda:{"max":gateway,"vk":None}, poll_seconds=60)
        completed, skipped = await scheduler.run_once()
        self.assertEqual((completed, skipped), (1, 0))
        self.assertEqual(executor.calls, [("max", max_item.schedule_id, "max")])
        self.assertEqual(self.store.get("max", "1", max_item.schedule_id).last_status, "ok")
        vk = self.store.get("vk", "2", vk_item.schedule_id)
        self.assertEqual(vk.last_status, "error")
        self.assertIn("unavailable", vk.last_error)

    async def test_manual_run_does_not_move_next_schedule(self):
        item = self.store.add("max", "1", "chat1", self.snapshot, "Europe/Moscow", "06:00", 1)
        before = item.next_run_utc
        executor = Executor(); gateway = Gateway()
        scheduler = MessengerScheduler(store=self.store, executor=executor, gateways=lambda:{"max":gateway}, poll_seconds=60)
        self.assertTrue(await scheduler.execute_now(item, gateway))
        after = self.store.get("max", "1", item.schedule_id).next_run_utc
        self.assertEqual(before, after)


if __name__ == "__main__": unittest.main()
