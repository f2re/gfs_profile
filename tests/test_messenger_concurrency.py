from __future__ import annotations

import asyncio
import threading
import time
import unittest
from dataclasses import dataclass

from messenger.contracts import CommonProductResult, NormalizedEvent, PlatformMessage
from messenger.router import MessengerRouter, RouterDependencies


@dataclass
class Point:
    lat: float
    lon: float
    label: str


class Parsed:
    location_query = "Москва"
    lead_hour = 24
    run = None
    lead_from_user = True


class Gateway:
    platform = "max"

    async def send_text(self, chat_id, text, **kwargs):
        return PlatformMessage("max", chat_id, f"m-{chat_id}")

    async def edit_text(self, chat_id, message_id, text, **kwargs):
        return PlatformMessage("max", chat_id, message_id)

    async def send_image(self, *args, **kwargs):
        return PlatformMessage("max", "x", "x")

    async def send_file(self, *args, **kwargs):
        return PlatformMessage("max", "x", "x")

    async def send_animation(self, *args, **kwargs):
        return PlatformMessage("max", "x", "x")

    async def answer_callback(self, *args, **kwargs):
        return None


class MessengerConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_profile_builder_respects_gfs_limit(self):
        point = Point(55.7, 37.6, "Москва")
        lock = threading.Lock()
        active = 0
        maximum = 0

        def builder(*args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return CommonProductResult("profile", "ok", [], {})

        router = MessengerRouter(
            RouterDependencies(
                geocode=lambda query, limit: [point],
                profile_builder=builder,
                profile_parser=lambda raw, default: Parsed(),
                canonical_leads=lambda: [24],
            ),
            max_concurrent_gfs=1,
            progress_interval_seconds=0.01,
        )
        gateway = Gateway()
        events = [
            NormalizedEvent("max", str(i), "TEXT", str(i), f"user:{i}", text="Москва +24")
            for i in (1, 2)
        ]
        await asyncio.gather(*(router.handle(event, gateway) for event in events))
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
