from __future__ import annotations

import asyncio
import base64
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from gfs_core import GfsProfileError, GfsRun, canonical_leads
from geocode import GeoPoint, parse_coordinates
from icing_config import ICING_PRODUCTS, IcingRequest
from messenger.callback_codec import decode_callback, encode_callback
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage, ProductAttachment, ProgressEvent
from messenger.icing_router import (
    IcingMessengerRouter, icing_owner, icing_payload, icing_request_from_payload,
    icing_card_keyboard, icing_card_text, icing_lead_keyboard, icing_menu_keyboard,
)
from messenger.icing_labels import BUTTON_LABELS as LABELS
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


def event(platform="max", text="/icing", payload=None, user="42", chat="1", location=None):
    kind = "CALLBACK" if payload else "LOCATION" if location is not None else "COMMAND" if text.startswith("/") else "TEXT"
    return NormalizedEvent(platform, "e", kind, user, chat,
        message_id="9" if payload else "8", text=text,
        command=text[1:].split()[0] if text.startswith("/") and not payload and location is None else None,
        callback_payload=payload, callback_id="cb" if payload else None, location=location)


def last_keyboard(gateway):
    return next(call[2] for call in reversed(gateway.calls) if hasattr(call[2], "rows"))


def icing_button(gateway, action, **fields):
    for row in last_keyboard(gateway).rows:
        for button in row:
            if not (button.payload or "").startswith("v1|icing|"):
                continue
            decoded_action, request = icing_request_from_payload(button.payload)
            if decoded_action == action and all(getattr(request, key) == value for key, value in fields.items()):
                return button
    raise AssertionError(f"Icing action {action} with {fields} not found")


class IcingRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.routers, self.built, self.created, self.checked = [], [], [], []

    def select_run(self, request):
        self.checked.append(request)
        return request.run or GfsRun("20261117", "06"), "verified test index"

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

    def new_router(self, builder=None, cls=IcingMessengerRouter, selector=None, geocoder=None):
        router = cls(RouterDependencies(geocode=geocoder or (lambda *_: (_ for _ in ()).throw(AssertionError("regional map must not geocode")))),
            recipes=UserRecipeStore(self.root / "recipes.db"), icing_builder=builder or self.builder,
            icing_run_selector=selector or self.select_run,
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
            self.assertEqual(self.built[-1].product, "composite")
            self.assertTrue(any(c[0] == "image" for c in gateway.calls))
            self.assertFalse(any(c[0] == "file" and c[1].endswith(".csv") for c in gateway.calls))
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[1], labels[2])
        self.assertTrue(all(not path.exists() for path in self.created))

    async def test_menu_checks_required_lead_and_pins_dates_before_build(self):
        router, gateway = self.new_router(), Gateway()
        await router.handle(event(), gateway)
        self.assertEqual([r.lead for r in self.checked], [24])
        texts = [c for c in gateway.calls if c[0] in {"text", "edit"}]
        self.assertEqual([c[0] for c in texts], ["text", "edit"])
        self.assertEqual(texts[0][3], texts[1][3])
        self.assertIn("17.11.2026 06:00 UTC", texts[-1][1])
        self.assertIn("18.11.2026 06:00 UTC", texts[-1][1])
        self.assertNotIn("последний опубликованный", texts[-1][1])
        _, request = icing_request_from_payload(last_keyboard(gateway).rows[0][0].payload)
        self.assertEqual(request.run, GfsRun("20261117", "06"))
        self.assertEqual(self.built, [])
        # Other platforms reuse the same recently checked metadata.
        await router.handle(event("vk"), Gateway("vk"))
        self.assertEqual(len(self.checked), 1)

    async def test_lead_switch_validates_requested_file_and_explains_fallback(self):
        current, older = GfsRun("20261118", "12"), GfsRun("20261118", "06")
        def selector(request):
            self.checked.append(request)
            if request.run == current and request.lead == 384:
                raise GfsProfileError("f384 не опубликован")
            return request.run or (older if request.lead == 384 else current), "index"
        router, gateway = self.new_router(selector=selector), Gateway()
        await router.handle(event(), gateway)
        request = IcingRequest(lead=384, run=current)
        payload = icing_payload("t", request, owner=icing_owner(event()))
        gateway.calls.clear()
        await router.handle(event(payload=payload), gateway)
        self.assertEqual(gateway.calls[0][0], "answer")
        text = gateway.calls[-1][1]
        self.assertIn("недоступны", text)
        self.assertIn("18.11.2026 06:00 UTC", text)
        self.assertIn("04.12.2026 06:00 UTC", text)
        _, actual = icing_request_from_payload(last_keyboard(gateway).rows[0][0].payload)
        self.assertEqual(actual.run, older)
        self.assertEqual(actual.lead, 384)
        self.assertEqual([(r.lead, r.run) for r in self.checked[-2:]], [(384, current), (384, None)])
        self.assertEqual(self.built, [])

    async def test_explicit_cycle_is_not_replaced_when_opening_card_or_exporting(self):
        router, gateway = self.new_router(), Gateway()
        request = IcingRequest(run=GfsRun("20261116", "18"))
        await router._icing_card(event(), gateway, request)
        self.assertIn("16.11.2026 18:00 UTC", gateway.calls[-1][1])
        self.assertEqual(self.checked, [])
        payload = last_keyboard(gateway).rows[0][0].payload
        await router.handle(event(payload=payload), gateway)
        await router.icing_wait_idle()
        self.assertEqual(self.built[-1].run, request.run)

    async def test_refresh_bypasses_short_latest_cycle_cache(self):
        runs = iter([GfsRun("20261118", "06"), GfsRun("20261118", "12")])
        router, gateway = self.new_router(selector=lambda r: (next(runs), "index")), Gateway()
        await router.handle(event(), gateway)
        refresh = icing_payload("u", IcingRequest(), owner=icing_owner(event()))
        await router.handle(event(payload=refresh), gateway)
        self.assertIn("18.11.2026 12:00 UTC", gateway.calls[-1][1])

    async def test_check_failure_shows_retry_without_runnable_unknown_cycle(self):
        def fail(_):
            raise GfsProfileError("NOMADS недоступен")
        router, gateway = self.new_router(selector=fail), Gateway()
        await router.handle(event(), gateway)
        self.assertIn("Не удалось проверить срок +24", gateway.calls[-1][1])
        self.assertIn("NOMADS недоступен", gateway.calls[-1][1])
        actions = [icing_request_from_payload(b.payload)[0] for row in last_keyboard(gateway).rows for b in row
                   if (b.payload or "").startswith("v1|icing|")]
        self.assertNotIn("r", actions)
        self.assertIn("u", actions)
        self.assertEqual(self.built, [])

    async def test_cancelling_metadata_check_prevents_stale_menu_reappearance(self):
        started, release = threading.Event(), threading.Event()
        def slow(request):
            started.set()
            release.wait(3)
            return GfsRun("20261118", "06"), "index"
        router, gateway = self.new_router(selector=slow), Gateway()
        task = asyncio.create_task(router.handle(event(), gateway))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            await router.handle(event(text="/status"), gateway)
            self.assertIn("проверяю публикацию", gateway.calls[-1][1])
            await router.handle(event(text="/cancel"), gateway)
            cancelled_count = len(gateway.calls)
        finally:
            release.set()
        await task
        self.assertEqual(len(gateway.calls), cancelled_count)
        self.assertIn("выбор сброшен", gateway.calls[-1][1])
        self.assertIsNone(router.sessions.get("max", "42", "1"))

    async def test_legacy_csv_button_opens_card_without_building_or_sending_files(self):
        router, gateway = self.new_router(), Gateway()
        payload = icing_payload("v", IcingRequest(run=GfsRun("20261117", "06")), owner=icing_owner(event()))
        await router.handle(event(payload=payload), gateway)
        self.assertIn("Численные таблицы убраны", gateway.calls[-1][1])
        self.assertFalse(any(c[0] in {"image", "file"} for c in gateway.calls))
        self.assertEqual(self.built, [])

    async def test_contextual_help_and_units_on_all_platforms(self):
        router = self.new_router()
        for platform in ("telegram", "max", "vk"):
            gateway = Gateway(platform)
            request = IcingRequest(product="slw_max", run=GfsRun("20261117", "06"))
            await router._icing_card(event(platform), gateway, request)
            help_button = next(b for row in last_keyboard(gateway).rows for b in row if b.text == LABELS["help"])
            await router.handle(event(platform, payload=help_button.payload), gateway)
            text = gateway.calls[-1][1]
            self.assertIn("NOAA", text)
            self.assertIn("г/м³", text)
            self.assertIn("https://", text)
            self.assertNotIn("дождевая фаза", text)
            payload = icing_payload("o", replace(request, product="godske_sum"), owner=icing_owner(event(platform)))
            await router.handle(event(platform, payload=payload), gateway)
            labels = [b.text for row in last_keyboard(gateway).rows for b in row]
            self.assertIn("10 дам", labels)
            self.assertIn("✓ 50 дам", labels)
            self.assertNotIn("гп.м", " ".join(labels))

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
        png_button = next(b for row in keyboard.rows for b in row if b.text == LABELS["png"])
        action, request = icing_request_from_payload(png_button.payload)
        self.assertEqual(action, "i")
        self.assertEqual(request.run, GfsRun("20261117", "06"))
        gateway.calls.clear()
        await router.handle(event(payload=png_button.payload), gateway)
        await router.icing_wait_idle()
        self.assertEqual(self.built[-1].run, request.run)
        self.assertFalse(any(c[0] == "image" for c in gateway.calls))
        self.assertEqual(len([c for c in gateway.calls if c[0] == "file" and c[1].endswith(".png")]), 1)

    async def test_adjacent_result_forecast_builds_directly_on_same_cycle(self):
        router = self.new_router()
        for platform in ("telegram", "max", "vk"):
            gateway = Gateway(platform)
            await router.handle(event(platform, text="/icing +24"), gateway)
            await router.icing_wait_idle()
            following = next(b for row in last_keyboard(gateway).rows for b in row
                             if b.text.endswith(" ›"))
            action, selected = icing_request_from_payload(following.payload)
            self.assertEqual(action, "n")
            self.assertEqual(selected.lead, 25)
            before = len(self.built)
            gateway.calls.clear()
            await router.handle(event(platform, payload=following.payload), gateway)
            await router.icing_wait_idle()
            self.assertEqual(len(self.built), before + 1)
            self.assertEqual(self.built[-1], selected)
            self.assertTrue(any(c[0] == "image" for c in gateway.calls))
            # Reuse the result/status message: a completed preflight must not be
            # left as a separate 'checking' message after the next image arrives.
            self.assertFalse(any(c[0] == "text" for c in gateway.calls))
            self.assertTrue(all(c[3] == "9" for c in gateway.calls if c[0] == "edit"))

    async def test_adjacent_result_fallback_requires_review_of_new_valid_time(self):
        current, older = GfsRun("20261118", "12"), GfsRun("20261118", "06")
        def selector(request):
            if request.run == current:
                raise GfsProfileError("Срок не опубликован")
            return older, "index"
        router, gateway = self.new_router(selector=selector), Gateway()
        selected = IcingRequest(lead=27, run=current)
        payload = icing_payload("n", selected, owner=icing_owner(event()))
        await router.handle(event(payload=payload), gateway)
        await router.icing_wait_idle()
        self.assertEqual(self.built, [])
        self.assertIn("19.11.2026 09:00 UTC", gateway.calls[-1][1])
        self.assertIn("недоступны", gateway.calls[-1][1])
        build = last_keyboard(gateway).rows[0][0]
        self.assertEqual(build.text, LABELS["build"])
        _, actual = icing_request_from_payload(build.payload)
        self.assertEqual(actual.run, older)

    async def test_help_does_not_wait_for_or_start_metadata_checks(self):
        started, release = threading.Event(), threading.Event()
        def slow(request):
            self.checked.append(request)
            started.set()
            release.wait(3)
            return GfsRun("20261118", "06"), "index"
        router, gateway = self.new_router(selector=slow), Gateway()
        help_payload = icing_payload("m", IcingRequest(product="slw_max"), owner=icing_owner(event()))
        await router.handle(event(payload=help_payload), gateway)
        self.assertEqual(self.checked, [])
        opening = asyncio.create_task(router.handle(event(), gateway))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            await asyncio.wait_for(router.handle(event(payload=help_payload), gateway), timeout=.5)
            self.assertIn("Как читать карту", gateway.calls[-1][1])
            help_count = len(gateway.calls)
        finally:
            release.set()
        await opening
        self.assertEqual(len(gateway.calls), help_count)
        self.assertEqual(len(self.checked), 1)

    async def test_double_click_metadata_check_shares_request_and_latest_card_wins(self):
        started, release = threading.Event(), threading.Event()
        def slow(request):
            self.checked.append(request)
            started.set()
            release.wait(3)
            return GfsRun("20261118", "06"), "index"
        router, gateway = self.new_router(selector=slow), Gateway()
        payload = icing_payload("t", IcingRequest(lead=48), owner=icing_owner(event()))
        first = asyncio.create_task(router.handle(event(payload=payload), gateway))
        second = None
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            second = asyncio.create_task(router.handle(event(payload=payload), gateway))
            await asyncio.sleep(.02)
        finally:
            release.set()
        await asyncio.gather(first, second)
        self.assertEqual(len(self.checked), 1)
        cards = [c for c in gateway.calls if c[0] == "edit" and "Прогноз +48 ч" in c[1]]
        self.assertEqual(len(cards), 1)
        self.assertFalse(router._icing_pending)

    async def test_metadata_timeout_signals_default_worker_to_stop_fallback_requests(self):
        stopped = threading.Event()
        def slow(request, *, cancel_event):
            if cancel_event.wait(1):
                stopped.set()
            raise GfsProfileError("Проверка прервана")
        with patch("messenger.icing_router.select_icing_run", side_effect=slow), \
             patch("messenger.icing_router._RUN_CHECK_TIMEOUT", .03):
            router = IcingMessengerRouter(RouterDependencies(geocode=lambda *_: []),
                recipes=UserRecipeStore(self.root / "recipes.db"), icing_builder=self.builder)
            self.routers.append(router)
            gateway = Gateway()
            await router.handle(event(), gateway)
            self.assertTrue(await asyncio.to_thread(stopped.wait, .5))
        self.assertIn("не ответил вовремя", gateway.calls[-1][1])
        self.assertFalse(router._icing_pending)
        self.assertEqual(self.built, [])

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
        router = self.new_router()
        for platform in ("telegram", "max", "vk"):
            gateway = Gateway(platform)
            await router.gfs_semaphore.acquire()
            try:
                await router.handle(event(platform, text="/icing +24"), gateway)
                status, cancel = icing_button(gateway, "s"), icing_button(gateway, "x")
                self.assertEqual((status.text, cancel.text), (LABELS["status"], LABELS["cancel"]))
                await router.handle(event(platform, payload=status.payload), gateway)
                self.assertIn("выполняется", gateway.calls[-1][1])
                await router.handle(event(platform, payload=cancel.payload), gateway)
                self.assertIsNone(router.sessions.get(platform, "42", "1"))
                self.assertTrue(any(b.payload == encode_callback("flow", "home")
                                    for row in last_keyboard(gateway).rows for b in row))
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

    async def test_full_icing_navigation_keeps_dates_point_and_home_on_three_platforms(self):
        from messenger.schedule_router import ScheduleMessengerRouter
        choices = [GeoPoint(55.7558, 37.6173, "Москва, Россия"), GeoPoint(56.1, 38.2, "Москва, другой пункт")]
        router = self.new_router(cls=ScheduleMessengerRouter, geocoder=lambda *_: choices)
        pages = (len(canonical_leads()) + 11) // 12
        for platform in ("telegram", "max", "vk"):
            gateway = Gateway(platform)
            owner = icing_owner(event(platform))

            async def click(action, **fields):
                button = icing_button(gateway, action, **fields)
                before = len(gateway.calls)
                await router.handle(event(platform, payload=button.payload), gateway)
                self.assertEqual(gateway.calls[before][0], "answer")
                return button

            def assert_controls():
                keyboard = last_keyboard(gateway)
                self.assertTrue(any(b.payload == encode_callback("flow", "home") for row in keyboard.rows for b in row))
                for row in keyboard.rows:
                    self.assertLessEqual(len(row), 3)
                    if len(row) == 3:
                        self.assertTrue(all("гПа" in b.text or "дам" in b.text for b in row))
                    for button in row:
                        if (button.payload or "").startswith("v1|icing|"):
                            self.assertLessEqual(len(button.payload.encode()), 64)
                            icing_request_from_payload(button.payload, owner=owner)

            await router.handle(event(platform), gateway)
            self.assertIn("17.11.2026 06:00 UTC", gateway.calls[-1][1])
            self.assertIn("18.11.2026 06:00 UTC", gateway.calls[-1][1])
            assert_controls()
            await click("a")
            self.assertIn("NOAA", gateway.calls[-1][1])
            await click("h")
            await click("g")
            geometry = {icing_request_from_payload(button.payload)[1].product
                        for row in last_keyboard(gateway).rows for button in row
                        if (button.payload or "").startswith("v1|icing|c|")}
            self.assertEqual(geometry, {"godske_sum", "godske_max", "godske_base", "godske_top", "godske_count"})
            assert_controls()
            await click("c", product="godske_max")
            await click("m")
            self.assertIn("дам", gateway.calls[-1][1])
            await click("c")
            await click("o")
            self.assertTrue(icing_button(gateway, "c", contour_step=500).text.startswith("✓ "))
            await click("c", contour_step=100)
            await click("p")
            self.assertTrue(any(b.action == "request_location" for row in last_keyboard(gateway).rows for b in row))
            await router.handle(event(platform, text="Москва"), gateway)
            await click("q", point=(55.7558, 37.6173))
            self.assertIn("Москва, Россия", gateway.calls[-1][1])
            await click("p")
            await router.handle(event(platform, text="", location=Location(59.9386, 30.3141)), gateway)
            selected = router.sessions.get(platform, "42", "1").params["request"]
            self.assertEqual((selected.product, selected.point, selected.contour_step),
                             ("godske_max", (59.9386, 30.3141), 100))
            await click("l0")
            self.assertTrue(icing_button(gateway, "t", lead=24).text.startswith("✓ +24"))
            await click("l1")
            for page in range(2, pages + 1):
                await click(f"l{page}")
            assert_controls()
            await click("t", lead=384)
            self.assertIn("03.12.2026 06:00 UTC", gateway.calls[-1][1])
            self.assertEqual(router.sessions.get(platform, "42", "1").params["request"], replace(selected, lead=384))
            await click("r")
            await router.icing_wait_idle()
            self.assertEqual(self.built[-1], replace(selected, lead=384))
            assert_controls()
            before = len(gateway.calls)
            await click("i")
            await router.icing_wait_idle()
            delivered = gateway.calls[before:]
            self.assertTrue(any(call[0] == "file" and call[1].endswith(".png") for call in delivered))
            self.assertFalse(any(call[0] == "file" and call[1].endswith(".csv") for call in delivered))
            await click("c")
            await click("p")
            await router.handle(event(platform, text=LABELS["back_card"]), gateway)
            self.assertEqual(router.sessions.get(platform, "42", "1").params["request"].point, selected.point)
            await click("p")
            await router.handle(event(platform, text=LABELS["home"]), gateway)
            self.assertIsNone(router.sessions.get(platform, "42", "1"))
            self.assertIn(encode_callback("product", "open", "icing"),
                          [b.payload for row in last_keyboard(gateway).rows for b in row])

    async def test_ambiguous_marker_choice_preserves_parameters_on_all_platforms(self):
        choices = [GeoPoint(55.7558, 37.6173, "Москва, Россия"),
                   GeoPoint(56.1, 38.2, "Москва, другой населённый пункт")]
        queries = []
        def geocoder(query, limit):
            queries.append((query, limit))
            return choices
        router = self.new_router(geocoder=geocoder)
        original = IcingRequest(product="slw_level", lead=48, level_hpa=700, contour_step=100,
                                bbox=(12.125, 75.125, 35.125, 77.125), run=GfsRun("20261117", "06"))
        for platform in ("telegram", "max", "vk"):
            gateway = Gateway(platform)
            payload = icing_payload("p", original, owner=icing_owner(event(platform)))
            await router.handle(event(platform, payload=payload), gateway)
            self.assertEqual(gateway.calls[-1][0], "edit")
            self.assertTrue(any(b.action == "request_location" for row in last_keyboard(gateway).rows for b in row))
            self.assertEqual(router.sessions.get(platform, "42", "1").step, "await_point")
            await router.handle(event(platform, text="Москва"), gateway)
            self.assertEqual(router.sessions.get(platform, "42", "1").step, "choose_point")
            choice = last_keyboard(gateway).rows[1][0]
            action, selected = icing_request_from_payload(choice.payload)
            self.assertEqual(action, "q")
            self.assertEqual(selected, replace(original, point=(56.1, 38.2)))
            self.assertEqual(len(self.built), 0)
            await router.handle(event(platform, payload=choice.payload), gateway)
            self.assertIn(choices[1].label, gateway.calls[-1][1])
            self.assertIn("56.1000, 38.2000", gateway.calls[-1][1])
            self.assertEqual(router.sessions.get(platform, "42", "1").params["request"], selected)
            # Lead selection keeps the marker and all unrelated request fields.
            change_lead = icing_payload("t", replace(selected, lead=72), owner=icing_owner(event(platform)))
            await router.handle(event(platform, payload=change_lead), gateway)
            built_request = icing_request_from_payload(last_keyboard(gateway).rows[0][0].payload)[1]
            self.assertEqual(built_request, replace(selected, lead=72))
        self.assertEqual(queries, [("Москва", 5)] * 3)

    async def test_coordinate_and_geolocation_marker_then_remove_on_all_platforms(self):
        queries = []
        def geocoder(query, limit):
            queries.append(query)
            return [parse_coordinates(query)]
        router = self.new_router(geocoder=geocoder)
        original = IcingRequest(product="slwp", run=GfsRun("20261117", "06"))
        for platform in ("telegram", "max", "vk"):
            gateway = Gateway(platform)
            owner = icing_owner(event(platform))
            await router.handle(event(platform, payload=icing_payload("p", original, owner=owner)), gateway)
            await router.handle(event(platform, text="55.7558 37.6173"), gateway)
            selected = router.sessions.get(platform, "42", "1").params["request"]
            self.assertEqual(selected, replace(original, point=(55.7558, 37.6173)))
            await router.handle(event(platform, payload=icing_payload("p", selected, owner=owner)), gateway)
            await router.handle(event(platform, text="", location=Location(59.93863, 30.31413)), gateway)
            selected = router.sessions.get(platform, "42", "1").params["request"]
            self.assertEqual(selected, replace(original, point=(59.9386, 30.3141)))
            await router.handle(event(platform, payload=icing_payload("r", selected, owner=owner)), gateway)
            await router.icing_wait_idle()
            self.assertEqual(self.built[-1], selected)
            await router.handle(event(platform, payload=icing_payload("p", selected, owner=owner)), gateway)
            remove = next(b for row in last_keyboard(gateway).rows for b in row if b.text == LABELS["remove_point"])
            await router.handle(event(platform, payload=remove.payload), gateway)
            self.assertEqual(router.sessions.get(platform, "42", "1").params["request"], original)
            self.assertIn("Пункт убран", gateway.calls[-1][1])
        self.assertEqual(queries, ["55.7558 37.6173"] * 3)

    async def test_point_outside_bbox_and_failed_search_keep_previous_selection(self):
        router = self.new_router(geocoder=lambda *_: [GeoPoint(20, 37, "Вне карты")])
        gateway = Gateway()
        original = IcingRequest(point=(55.75, 37.62), run=GfsRun("20261117", "06"))
        await router.handle(event(payload=icing_payload("p", original, owner=icing_owner(event()))), gateway)
        await router.handle(event(text="Не тот город"), gateway)
        self.assertIn("вне района", gateway.calls[-1][1])
        self.assertEqual(router.sessions.get("max", "42", "1").params["request"], original)
        await router.handle(event(text="", location=Location(20, 37)), gateway)
        self.assertIn("вне выбранного района", gateway.calls[-1][1])
        await router.handle(event(text="Назад к карте"), gateway)
        self.assertEqual(router.sessions.get("max", "42", "1").params["request"], original)
        self.assertEqual(router.sessions.get("max", "42", "1").step, "params")

    async def test_marker_selection_button_survives_restart_and_checks_owner(self):
        request = IcingRequest(product="slw_height", lead=384, run=GfsRun("20261117", "06"), point=(55.7558, 37.6173))
        payload = icing_payload("q", request, owner=icing_owner(event()))
        router, gateway = self.new_router(), Gateway()
        await router.handle(event(payload=payload), gateway)
        self.assertEqual(router.sessions.get("max", "42", "1").params["request"], request)
        self.assertIn("Выбранный пункт", gateway.calls[-1][1])
        await router.handle(event(payload=payload, user="43"), gateway)
        self.assertIn("другому пользователю", gateway.calls[-1][1])
        self.assertIsNone(router.sessions.get("max", "43", "1"))
        self.assertFalse(self.checked)

    async def test_cancel_or_back_during_geocode_cannot_restore_marker_flow(self):
        for cancel in (True, False):
            started, release = threading.Event(), threading.Event()
            def geocoder(*_):
                started.set()
                release.wait(3)
                return [GeoPoint(55.75, 37.62, "Москва")]
            router, gateway = self.new_router(geocoder=geocoder), Gateway()
            request = IcingRequest(run=GfsRun("20261117", "06"), point=(56, 38))
            await router.handle(event(payload=icing_payload("p", request, owner=icing_owner(event()))), gateway)
            task = asyncio.create_task(router.handle(event(text="Москва"), gateway))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                if cancel:
                    await router.handle(event(text="/cancel"), gateway)
                else:
                    await router.handle(event(text="Отмена выбора пункта"), gateway)
                count = len(gateway.calls)
            finally:
                release.set()
            await task
            self.assertEqual(len(gateway.calls), count)
            state = router.sessions.get("max", "42", "1")
            if cancel:
                self.assertIsNone(state)
            else:
                self.assertEqual(state.params["request"], request)
                self.assertEqual(state.step, "params")

    async def test_cancel_while_search_status_is_sending_does_not_call_geocoder(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowGateway(Gateway):
            async def send_text(self, chat_id, text, **kwargs):
                if "Ищу пункт" in text:
                    entered.set()
                    await release.wait()
                return await super().send_text(chat_id, text, **kwargs)
        queries = []
        router, gateway = self.new_router(geocoder=lambda *args: queries.append(args) or []), SlowGateway()
        request = IcingRequest(run=GfsRun("20261117", "06"))
        await router.handle(event(payload=icing_payload("p", request, owner=icing_owner(event()))), gateway)
        task = asyncio.create_task(router.handle(event(text="Москва"), gateway))
        await entered.wait()
        await router.handle(event(text="/cancel"), gateway)
        release.set()
        await task
        self.assertFalse(queries)
        self.assertIsNone(router.sessions.get("max", "42", "1"))

    async def test_cancel_during_point_callback_ack_cannot_restore_selection(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowGateway(Gateway):
            async def answer_callback(self, event, *, text=None):
                entered.set()
                await release.wait()
                await super().answer_callback(event, text=text)
        router, gateway = self.new_router(), SlowGateway()
        request = IcingRequest(run=GfsRun("20261117", "06"), point=(55.75, 37.62))
        await router._icing_card(event(), gateway, request)
        task = asyncio.create_task(router.handle(event(payload=icing_payload("p", request, owner=icing_owner(event()))), gateway))
        await entered.wait()
        await router.handle(event(text="/cancel"), gateway)
        count = len(gateway.calls)
        release.set()
        await task
        self.assertIsNone(router.sessions.get("max", "42", "1"))
        self.assertEqual([call[0] for call in gateway.calls[count:]], ["answer"])


class IcingCallbackTests(unittest.TestCase):
    def test_all_products_levels_and_end_lead_fit_telegram_payload(self):
        request = IcingRequest(lead=384, run=GfsRun("20261117", "18"),
                               bbox=(-179.125, -1.125, 34.125, 80.125))
        for product in ICING_PRODUCTS:
            request = replace(request, product=product)
            for action in ("r", "i", "v", "l18", "c", "m", "a", "t", "n", "u"):
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
                    if (button.payload or "").startswith("v1|icing|t|"):
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
        self.assertIn("Пункт на карте", text)
        self.assertIn("Меню", text)
        self.assertEqual(len(icing_menu_keyboard(IcingRequest()).rows), 6)
        first = icing_menu_keyboard(IcingRequest()).rows[0][0]
        self.assertEqual(icing_request_from_payload(first.payload)[1].product, "composite")

    def test_marker_wire_roundtrips_signed_coordinates_in64bytes_and_keeps_old_wire(self):
        for bbox, point in (((-180, -1, 34, 80), (55.7558, -37.6173)),
                            ((1, 180, 34, 80), (34, 180))):
            request = IcingRequest(product="slw_max", lead=384, bbox=bbox, point=point,
                                   run=GfsRun("20261117", "18"))
            for action in ("r", "i", "l18", "p", "q", "d", "n", "u"):
                payload = icing_payload(action, request, owner=icing_owner(event()))
                self.assertLessEqual(len(payload.encode("utf-8")), 64)
                self.assertEqual(icing_request_from_payload(payload, owner=icing_owner(event())), (action, request))
            raw = base64.urlsafe_b64decode(decode_callback(payload).value + "=")
            self.assertEqual(len(raw), 38)
            old = replace(request, point=None)
            payload = icing_payload("r", old, owner=icing_owner(event()))
            raw = base64.urlsafe_b64decode(decode_callback(payload).value + "=")
            self.assertEqual(len(raw), 35)
            self.assertEqual(icing_request_from_payload(payload)[1], old)

    def test_callbacks_from_before_composite_keep_the_original_product_indices(self):
        # Fixed tokens from the previous 35-byte wire format, not regenerated
        # through today's encoder: adding a primary product must not shift them.
        for token, product in (
            ("v1|icing|c|AAAAAAAAAAABABgDUgFBQAAAQqoAAEIiAABCjQAAATUo_gw", "godske_sum"),
            ("v1|icing|c|AAAAAAAAAAAJABgDUgFBQAAAQqoAAEIiAABCjQAAATUo_gw", "agreement"),
        ):
            action, request = icing_request_from_payload(token)
            self.assertEqual(action, "c")
            self.assertEqual(request.product, product)
            self.assertEqual(request.run, GfsRun("20261118", "12"))
            self.assertEqual(request.bbox, (12., 85., 40.5, 70.5))
            self.assertEqual(request.lead, 24)
            self.assertIsNone(request.point)

    def test_unknown_marker_wire_version_is_rejected(self):
        payload = icing_payload("q", IcingRequest(point=(55.75, 37.62)))
        value = decode_callback(payload).value
        raw = base64.urlsafe_b64decode(value + "=")
        token = base64.urlsafe_b64encode(bytes([3]) + raw[1:]).decode().rstrip("=")
        with self.assertRaisesRegex(ValueError, "версия"):
            icing_request_from_payload(encode_callback("icing", "q", token))

    def test_dated_lead_buttons_cross_year_and_remain_large(self):
        request = IcingRequest(run=GfsRun("20261231", "18"))
        keyboard = icing_lead_keyboard(request)
        labels = [b.text for row in keyboard.rows for b in row]
        self.assertIn("✓ +24 · 01.01 18:00", labels)
        self.assertTrue(all(len(row) <= 2 for row in keyboard.rows))
        self.assertIn("01.01.2027 18:00 UTC", icing_card_text(request))

    def test_card_uses_decametres_without_altering_internal_step(self):
        request = IcingRequest(product="godske_sum", contour_step=500, run=GfsRun("20261118", "06"))
        self.assertIn("через 50 дам", icing_card_text(request))
        self.assertEqual(request.contour_step, 500)


if __name__ == "__main__":
    unittest.main()
