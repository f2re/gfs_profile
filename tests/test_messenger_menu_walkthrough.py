"""Walk the production router chain, where product-specific tests miss collisions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geocode import GeoPoint
from gfs_core import GfsRun, canonical_leads
from messenger.callback_codec import decode_callback, encode_callback
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage
from messenger.menu_catalog import PRODUCT_LABELS
from messenger.router import RouterDependencies
from messenger.schedule_router import ScheduleMessengerRouter
from messenger.state import FlowState
from messenger.user_recipes import UserRecipeStore


class Gateway:
    def __init__(self, platform):
        self.platform, self.calls = platform, []

    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        self.calls.append(("text", text, keyboard))
        return PlatformMessage(self.platform, chat_id, str(len(self.calls)))

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        self.calls.append(("edit", text, keyboard))
        return PlatformMessage(self.platform, chat_id, message_id)

    async def answer_callback(self, event, *, text=None):
        self.calls.append(("answer", text, None))

    async def send_image(self, *args, **kwargs):
        raise AssertionError("Walkthrough builders return no attachments")

    send_file = send_image
    send_animation = send_image

    def keyboard(self):
        return next(call[2] for call in reversed(self.calls) if call[2] is not None)


class ProductionMenuWalkthroughTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.built = []
        self.store = UserRecipeStore(Path(self.tmp.name) / "menus.db")

        def builder(product):
            def build(*args, **kwargs):
                self.built.append(product)
                return CommonProductResult(product, f"GFS data · {product}", [], {})
            return build

        def geocode(query, limit):
            return [GeoPoint(56.86, 35.91, "Тверь", "test")]

        self.router = ScheduleMessengerRouter(
            RouterDependencies(geocode=geocode, profile_builder=builder("profile"),
                canonical_leads=canonical_leads, latest_run_for_lead=lambda lead: GfsRun("20261118", "00")),
            recipes=self.store, aero_builder=builder("aero"), windgram_builder=builder("windgram"),
            cloudgram_builder=builder("cloudgram"), icing_builder=builder("icing"), map_builder=builder("map"),
            icing_run_selector=lambda request: (request.run or GfsRun("20261118", "00"), ""),
            meteogram_builder=builder("meteogram"), route_builder=builder("route"),
            progress_interval_seconds=0.25,
        )

    async def asyncTearDown(self):
        await self.router.shutdown_icing()
        self.tmp.cleanup()

    def event(self, platform, user, *, payload=None, text=None, location=None):
        command = text[1:].split()[0] if text and text.startswith("/") else None
        kind = "CALLBACK" if payload else "LOCATION" if location else "COMMAND" if command else "TEXT"
        return NormalizedEvent(platform, "event", kind, user, user, text=text, command=command,
            callback_payload=payload, callback_id="cb" if payload else None, location=location)

    async def click(self, gateway, user, payload):
        before = len(gateway.calls)
        await self.router.handle(self.event(gateway.platform, user, payload=payload), gateway)
        self.assertEqual(gateway.calls[before][0], "answer", payload)

    def assert_keyboard(self, keyboard):
        for row in keyboard.rows:
            self.assertLessEqual(len(row), 3)
            if len(row) == 3:
                self.assertTrue(all(any(c.isdigit() for c in button.text) for button in row))
        self.assertTrue(any(b.payload == encode_callback("flow", "home") for row in keyboard.rows for b in row))

    async def test_all_products_open_configure_build_and_return_home(self):
        # Each product is opened through the full production chain, on all adapters.
        products = ("profile", "aero", "windgram", "cloudgram", "map", "meteogram", "icing", "route")
        for platform in ("telegram", "max", "vk"):
            for product in products:
                with self.subTest(platform=platform, product=product):
                    user, gateway = f"{platform}-{product}", Gateway(platform)
                    await self.router.handle(self.event(platform, user, text="/start"), gateway)
                    home = gateway.keyboard()
                    self.assertTrue(all(len(row) <= 2 for row in home.rows))
                    button = next(b for row in home.rows for b in row if b.payload == encode_callback("product", "open", product))
                    self.assertEqual(button.text, PRODUCT_LABELS[product])
                    await self.click(gateway, user, button.payload)
                    self.assertEqual(self.router.sessions.get(platform, user, user).product, product)
                    self.assert_keyboard(gateway.keyboard())
                    if product == "icing":
                        await self.click(gateway, user, gateway.keyboard().rows[0][0].payload)
                        run_payload = gateway.keyboard().rows[0][0].payload
                    else:
                        await self.router.handle(self.event(platform, user, location=Location(55.75, 37.62)), gateway)
                        if product == "route":
                            await self.router.handle(self.event(platform, user, text="Тверь"), gateway)
                        if product in {"profile", "aero"}:
                            run_payload = encode_callback(product, "lead", 24)
                        else:
                            run_payload = encode_callback("meteo" if product == "meteogram" else product, "run")
                    await self.click(gateway, user, run_payload)
                    await self.router.icing_wait_idle()
                    self.assertEqual(self.built[-1], product)
                    self.assert_keyboard(gateway.keyboard())
                    await self.click(gateway, user, encode_callback("flow", "home"))
                    self.assertIsNone(self.router.sessions.get(platform, user, user))
                    self.assertTrue(gateway.calls[-1][1].startswith("GFS data"))

    async def test_profile_command_and_old_home_buttons_survive_full_router(self):
        gateway, user = Gateway("max"), "fresh"
        await self.router.handle(self.event("max", user, text="/profile"), gateway)
        self.assertEqual(self.router.sessions.get("max", user, user).product, "profile")
        for payload in (encode_callback("settings", "home"), encode_callback("schedule", "home"), encode_callback("flow", "home")):
            await self.click(gateway, user, payload)
            self.assertIn(encode_callback("product", "open", "profile"), [b.payload for row in gateway.keyboard().rows for b in row])

    async def test_meteogram_submenus_back_and_cancel_preserve_point(self):
        gateway, user = Gateway("vk"), "meteo-options"
        await self.click(gateway, user, encode_callback("product", "open", "meteogram"))
        await self.router.handle(self.event("vk", user, location=Location(55.75, 37.62)), gateway)
        for action in ("model", "period", "format"):
            await self.click(gateway, user, encode_callback("meteo", action))
            self.assert_keyboard(gateway.keyboard())
            await self.click(gateway, user, encode_callback("meteo", "back"))
            state = self.router.sessions.get("vk", user, user)
            self.assertEqual((state.product, state.point.lat, state.point.lon), ("meteogram", 55.75, 37.62))
        await self.click(gateway, user, encode_callback("meteo", "cancel"))
        self.assertIsNone(self.router.sessions.get("vk", user, user))
        self.assert_keyboard(gateway.keyboard())

    async def test_disabled_source_and_restarted_schedule_have_a_visible_way_out(self):
        gateway, user = Gateway("max"), "stale"
        self.router.sessions.set("max", user, user, FlowState(product="weathernext3", step="params"))
        with patch.dict("os.environ", {"WEATHERNEXT3_ENABLED": "0"}):
            await self.click(gateway, user, encode_callback("flow", "home"))
        self.assertTrue(gateway.calls[-1][1].startswith("GFS data"))
        for action, value in (("freq", 1), ("time", "06:00"), ("save", None)):
            await self.click(gateway, user, encode_callback("schedule", action, value))
            self.assertIn("Выбор устарел", gateway.calls[-1][1])
            self.assertTrue(any(b.text == "Меню" for row in gateway.keyboard().rows for b in row))
        await self.click(gateway, user, encode_callback("flow", "help"))
        self.assertIn("/icing", gateway.calls[-1][1])
        self.assert_keyboard(gateway.keyboard())

    async def test_every_profile_and_aero_lead_page_keeps_last_term_accessible(self):
        for product in ("profile", "aero"):
            gateway, user = Gateway("max"), f"pages-{product}"
            await self.click(gateway, user, encode_callback("product", "open", product))
            await self.router.handle(self.event("max", user, location=Location(55.75, 37.62)), gateway)
            seen = set()
            pages = (len(canonical_leads()) + 11) // 12
            for page in range(1, pages + 1):
                await self.click(gateway, user, encode_callback(product, "leadpage", page))
                for row in gateway.keyboard().rows:
                    for button in row:
                        if button.payload:
                            callback = decode_callback(button.payload)
                            if callback.scope == product and callback.action == "lead":
                                seen.add(int(callback.value))
            self.assertEqual(seen, set(canonical_leads()))
            self.assertIn(384, seen)
            self.assertNotIn(122, seen)
