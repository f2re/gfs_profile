from __future__ import annotations

import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from messenger.router import MessengerRouter, RouterDependencies
from messenger.runtime_resources import RuntimeResources


class RuntimeResourcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_gfs_gate_is_shared_between_routers(self) -> None:
        resources = RuntimeResources.from_limits(gfs=1, geocode=2, meteogram=2)
        deps1 = RouterDependencies(geocode=lambda query, limit: [])
        deps2 = RouterDependencies(geocode=lambda query, limit: [])
        router1 = resources.configure_router(MessengerRouter(deps1))
        router2 = resources.configure_router(MessengerRouter(deps2))
        self.assertIs(router1.gfs_semaphore, router2.gfs_semaphore)
        self.assertIs(router1.runtime_resources, resources)
        self.assertEqual(resources.snapshot(), {"gfs": 1, "geocode": 2, "meteogram": 2})

        lock = threading.Lock()
        active = 0
        maximum = 0

        async def work(router) -> None:
            nonlocal active, maximum
            async with router.gfs_semaphore:
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                await asyncio.sleep(0.04)
                with lock:
                    active -= 1

        await asyncio.gather(work(router1), work(router2))
        self.assertEqual(maximum, 1)

    async def test_blocking_geocoder_uses_same_process_gate_for_all_routers(self) -> None:
        resources = RuntimeResources.from_limits(gfs=2, geocode=1, meteogram=2)
        lock = threading.Lock()
        active = 0
        maximum = 0

        def geocode(query, limit):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return []

        router1 = resources.configure_router(MessengerRouter(RouterDependencies(geocode=geocode)))
        router2 = resources.configure_router(MessengerRouter(RouterDependencies(geocode=geocode)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda fn: fn("Москва", 5), [router1.deps.geocode, router2.deps.geocode]))
        self.assertEqual(maximum, 1)

    async def test_meteogram_gate_has_its_own_limit(self) -> None:
        resources = RuntimeResources.from_limits(gfs=3, geocode=2, meteogram=1)
        active = 0
        maximum = 0

        async def work() -> None:
            nonlocal active, maximum
            async with resources.meteogram_semaphore:
                active += 1
                maximum = max(maximum, active)
                await asyncio.sleep(0.03)
                active -= 1

        await asyncio.gather(work(), work())
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
