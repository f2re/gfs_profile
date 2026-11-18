from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from gfs_core import GfsRun, canonical_leads
from icing_config import ICING_PRODUCTS, IcingRequest
from messenger.callback_codec import decode_callback, encode_callback
from messenger.contracts import CommonProductResult, NormalizedEvent, PlatformMessage, ProductAttachment, ProgressEvent
from messenger.icing_router import (
    IcingMessengerRouter, icing_owner, icing_payload, icing_request_from_payload,
    icing_card_keyboard, icing_lead_keyboard, icing_menu_keyboard,
)
from messenger.router import RouterDependencies
from messenger.user_recipes import UserRecipeStore


class Gateway:
    def __init__(self, platform="max"):
        self.platform, self.calls, self.number = platform, [], 0

    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        self.number += 1
        self.calls.append(("text", text, keyboard, str(self.number)))
        return PlatformMessage(self.platform, chat_id, str(self.number))

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        self.calls.append(("edit", text, keyboard, message_id))
        return PlatformMessage(self.platform, chat_id, message_id)

    async def send_image(self, chat_id, path, *, caption=""):
        self.calls.append(("image", str(path), caption, None))
        return PlatformMessage(self.platform, chat_id, "image")

    async def send_file(self, chat_id, path, *, caption="", filename=None):
        self.calls.append(("file", str(path), caption, filename))
        return PlatformMessage(self.platform, chat_id, "file")

    async def answer_callback(self, event, *, text=None):
        self.calls.append(("answer", text, None, event.callback_id))


def event(platform="max", text="/icing", payload=None, user="42", chat="1"):
    return NormalizedEvent(platform, "e", "CALLBACK" if payload else "COMMAND", user, chat,
        message_id="9" if payload else "8", text=text,
        command=text[1:].split()[0] if text.startswith("/") and not payload else None,
        callback_payload=payload, callback_id="cb" if payload else None)


def last_keyboard(gateway):
    return next(call[2] for call in reversed(gateway.calls) if hasattr(call[2], "rows"))


class IcingRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.routers, self.built, self.created = [], [], []

    def builder(self, request, *, progress_callback=None, cancel_event=None):
        self.built.append(request)
        if progress_callback:
            progress_callback(ProgressEvent("render", "Формирую PNG и CSV"))
        number = len(self.built)
        png, csv = self.root / f"{number}.png", self.root / f"{number}.csv"
        png.write_bytes(b"png")
        csv.write_text("lat,lon,value\n55,37,12\n")
        self.created.extend([png, csv])
        return CommonProductResult("icing", "GFS: модельный прогноз · TEST", [
            ProductAttachment("image", png, png.name), ProductAttachment("file", csv, csv.name)
        ], {"run_date": "20261117", "run_cycle": "06"})

    def new_router(self, builder=None, cls=IcingMessengerRouter):
        router = cls(RouterDependencies(geocode=lambda *_: (_ for _ in ()).throw(AssertionError("regional map must not geocode"))),
            recipes=UserRecipeStore(self.root / "recipes.db"), icing_builder=builder or self.builder,
            max_concurrent_gfs=1, progress_interval_seconds=.25)
        self.routers.append(router)
        return router

    async def asyncTearDown(self):
        for router in self.routers:
            await router.shutdown_icing()
        self.tmp.cleanup()

    async def test_same_menu_and_product_delivery_on_three_platforms(self):
        router = self.new_router()
        labels = []
        for platform in ("telegram", "max", "vk"):
            gateway = Gateway(platform)
            await router.handle(event(platform), gateway)
            keyboard = last_keyboard(gateway)
            labels.append([[b.text for b in row] for row in keyboard.rows])
            self.assertNotIn("Укажите город", gateway.calls[-1][1])
            state = router.sessions.get(platform, "42", "1")
            self.assertIsNone(state.point)
            payload = keyboard.rows[0][0].payload
            gateway.calls.clear()
            await router.handle(event(platform, payload=payload), gateway)
            self.assertEqual(gateway.calls[0][0], "answer")
            build = last_keyboard(gateway).rows[0][0].payload
            await router.handle(event(platform, payload=build), gateway)
            await router.icing_wait_idle()
            self.assertEqual(self.built[-1].product, "slw_level")
            self.assertTrue(any(c[0] == "image" for c in gateway.calls))
            self.assertTrue(any(c[0] == "file" and c[1].endswith(".csv") for c in gateway.calls))
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[1], labels[2])
        self.assertTrue(all(not path.exists() for path in self.created))

    async def test_stateless_custom_request_survives_router_restart(self):
        first, gateway = self.new_router(), Gateway()
        request = IcingRequest(product="godske_max", lead=384, level_hpa=700, contour_step=100,
            bbox=(12.125, 75.125, 35.125, 77.125), run=GfsRun("20261117", "06"))
        await first._icing_card(event(), gateway, request)
        payload = last_keyboard(gateway).rows[0][0].payload
        fresh = self.new_router()
        await fresh.handle(event(payload=payload), gateway)
        await fresh.icing_wait_idle()
        self.assertEqual(self.built[-1], request)
        await fresh.handle(event(payload=payload, user="43"), gateway)
        await fresh.handle(event(payload=payload, chat="2"), gateway)
        self.assertEqual(len(self.built), 1)
        self.assertIn("другому", gateway.calls[-1][1])

    async def test_result_exports_retain_actual_cycle_and_png_is_document(self):
        router, gateway = self.new_router(), Gateway()
        await router.handle(event(text="/icing product=slwp +24"), gateway)
        await router.icing_wait_idle()
        keyboard = last_keyboard(gateway)
        png_button = next(b for row in keyboard.rows for b in row if b.text == "PNG целиком")
        action, request = icing_request_from_payload(png_button.payload)
        self.assertEqual(action, "i")
        self.assertEqual(request.run, GfsRun("20261117", "06"))
        gateway.calls.clear()
        await router.handle(event(payload=png_button.payload), gateway)
        await router.icing_wait_idle()
        self.assertEqual(self.built[-1].run, request.run)
        self.assertFalse(any(c[0] == "image" for c in gateway.calls))
        self.assertEqual(len([c for c in gateway.calls if c[0] == "file" and c[1].endswith(".png")]), 1)

    async def test_cancel_keeps_shared_capacity_until_thread_exits(self):
        started, release, second = threading.Event(), threading.Event(), threading.Event()
        active, maximum, lock = 0, 0, threading.Lock()
        def slow(request, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            if request.lead == 24:
                started.set()
                release.wait(4)
            else:
                second.set()
            try:
                return self.builder(request, **kwargs)
            finally:
                with lock:
                    active -= 1
        router, gateway = self.new_router(slow), Gateway()
        try:
            await router.handle(event(text="/icing +24"), gateway)
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            await router.handle(event(text="/icing +24"), gateway)
            self.assertEqual(len(router.icing_jobs), 1)
            await router.handle(event(text="/cancel"), gateway)
            await router.handle(event(text="/icing +48", user="43"), gateway)
            await asyncio.sleep(.03)
            self.assertFalse(second.is_set())
            self.assertEqual(len(router.icing_jobs), 2)
        finally:
            release.set()
        await router.icing_wait_idle()
        self.assertEqual(maximum, 1)
        self.assertEqual(len([c for c in gateway.calls if c[0] == "image"]), 1)
        self.assertFalse(router.icing_jobs)
        self.assertTrue(all(not path.exists() for path in self.created))

    async def test_status_and_cancellation_before_slot_do_not_start_builder(self):
        router, gateway = self.new_router(), Gateway()
        await router.gfs_semaphore.acquire()
        try:
            await router.handle(event(text="/icing +24"), gateway)
            await router.handle(event(text="/status"), gateway)
            self.assertIn("выполняется", gateway.calls[-1][1])
            await router.handle(event(text="/cancel"), gateway)
        finally:
            router.gfs_semaphore.release()
        await router.icing_wait_idle()
        self.assertEqual(self.built, [])

    async def test_errors_are_explained_without_stuck_job(self):
        def fail(*args, **kwargs):
            raise RuntimeError("Поля выбранного срока не опубликованы")
        router, gateway = self.new_router(fail), Gateway()
        await router.handle(event(text="/icing +24"), gateway)
        await router.icing_wait_idle()
        self.assertFalse(router.icing_jobs)
        self.assertIn("не опубликованы", gateway.calls[-1][1])
        await router.handle(event(text="/icing level=123"), gateway)
        self.assertIn("Уровень", gateway.calls[-1][1])

    async def test_production_schedule_chain_exposes_icing_from_start(self):
        from messenger.schedule_router import ScheduleMessengerRouter
        router, gateway = self.new_router(cls=ScheduleMessengerRouter), Gateway()
        await router.handle(event(text="/start"), gateway)
        payloads = [b.payload for row in last_keyboard(gateway).rows for b in row]
        self.assertIn(encode_callback("product", "open", "icing"), payloads)
        await router.handle(event(payload=encode_callback("product", "open", "icing")), gateway)
        self.assertIn("Выберите карту", gateway.calls[-1][1])

    async def test_common_map_menu_has_regional_entry_without_changing_map_fields(self):
        from messenger.map_router import MapMessengerRouter, _keyboard
        from messenger.map_service import DEFAULT_MAP_PARAMS
        keyboard = _keyboard(DEFAULT_MAP_PARAMS)
        payloads = [b.payload for row in keyboard.rows for b in row]
        self.assertIn(encode_callback("product", "open", "icing"), payloads)
        self.assertIn(encode_callback("map", "run"), payloads)
        router, gateway = self.new_router(cls=MapMessengerRouter), Gateway()
        await router.handle(event(payload=encode_callback("product", "open", "icing")), gateway)
        self.assertIn("Выберите карту", gateway.calls[-1][1])


class IcingCallbackTests(unittest.TestCase):
    def test_all_products_levels_and_end_lead_fit_telegram_payload(self):
        request = IcingRequest(lead=384, run=GfsRun("20261117", "18"),
                               bbox=(-179.125, -1.125, 34.125, 80.125))
        for product in ICING_PRODUCTS:
            request = replace(request, product=product)
            for action in ("r", "i", "v", "l18", "c", "m"):
                payload = icing_payload(action, request, owner=icing_owner(event()))
                self.assertLessEqual(len(payload.encode()), 64)
                decoded_action, decoded = icing_request_from_payload(payload, owner=icing_owner(event()))
                self.assertEqual((decoded_action, decoded), (action, request))

    def test_lead_pages_cover_canonical_grid_including384(self):
        expected = set(canonical_leads())
        found = set()
        for page in range(1, 20):
            keyboard = icing_lead_keyboard(IcingRequest(), page)
            for row in keyboard.rows:
                for button in row:
                    if button.text.startswith("+"):
                        found.add(icing_request_from_payload(button.payload)[1].lead)
        self.assertEqual(found, expected)
        self.assertIn(384, found)
        self.assertNotIn(122, found)

    def test_corrupt_token_and_owner_are_rejected(self):
        with self.assertRaises(ValueError):
            icing_request_from_payload("v1|icing|r|invalid")
        payload = icing_payload("r", IcingRequest(), owner=icing_owner(event()))
        with self.assertRaisesRegex(ValueError, "другому"):
            icing_request_from_payload(payload, owner=icing_owner(event(user="different")))

    def test_menu_contains_no_unimplemented_sources(self):
        text = " ".join(b.text for row in icing_menu_keyboard(IcingRequest()).rows for b in row)
        self.assertNotIn("WAFS", text)
        self.assertNotIn("осадки", text)
        self.assertEqual(len(icing_menu_keyboard(IcingRequest()).rows), 4)


if __name__ == "__main__":
    unittest.main()
