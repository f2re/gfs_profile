from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage
from messenger.route_router import RouteMessengerRouter
from messenger.router import RouterDependencies
from messenger.route_service import ParsedRouteInput
from messenger.user_recipes import UserRecipeStore


class Point:
    def __init__(self, lat, lon, label):
        self.lat, self.lon, self.label, self.source = lat, lon, label, "test"


class Gateway:
    def __init__(self, platform="max"):
        self.platform = platform; self.calls = []; self.counter = 0
    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        self.counter += 1; self.calls.append(("send_text", text, keyboard)); return PlatformMessage(self.platform, chat_id, str(self.counter))
    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        self.calls.append(("edit_text", text, keyboard)); return PlatformMessage(self.platform, chat_id, str(message_id))
    async def send_image(self, chat_id, path, *, caption=""): self.calls.append(("image", str(path), caption)); return PlatformMessage(self.platform, chat_id, "i")
    async def send_file(self, chat_id, path, *, caption="", filename=None): self.calls.append(("file", str(path), caption)); return PlatformMessage(self.platform, chat_id, "f")
    async def send_animation(self, chat_id, path, *, caption=""): return PlatformMessage(self.platform, chat_id, "a")
    async def answer_callback(self, event, *, text=None): self.calls.append(("answer", text, None))


class RouteRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.origin = Point(55.75, 37.62, "Москва")
        self.destination = Point(59.94, 30.31, "Санкт-Петербург")
        self.built = []
        def geocode(query, limit):
            return [self.destination if "Петер" in query else self.origin]
        def builder(origin, destination, lead, speed, mode, spatial_step, run, *, progress_callback=None):
            self.built.append((origin.label, destination.label, lead, speed, mode, spatial_step, run))
            return CommonProductResult("route", "ROUTE SUMMARY", [], {"max_lead": lead + 3})
        self.router = RouteMessengerRouter(
            RouterDependencies(geocode=geocode),
            recipes=self.store,
            route_builder=builder,
            progress_interval_seconds=0.01,
        )
        self.gateway = Gateway("max")

    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_direct_route_runs_builder_and_saves_both_endpoints(self):
        event = NormalizedEvent("max", "1", "COMMAND", "42", "chat", text="/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro", command="route")
        await self.router.handle(event, self.gateway)
        self.assertEqual(self.built[-1][:6], ("Москва", "Санкт-Петербург", 24, 300, "pro", 50))
        recipe = self.store.latest_for_product("max", "42", "route")
        self.assertEqual(recipe.params["origin"]["label"], "Москва")
        self.assertEqual(recipe.params["destination"]["label"], "Санкт-Петербург")
        self.assertNotIn("run", recipe.params)

    async def test_location_can_be_route_origin(self):
        await self.router.handle(NormalizedEvent("max", "2", "COMMAND", "42", "chat", text="/route", command="route"), self.gateway)
        await self.router.handle(NormalizedEvent("max", "3", "LOCATION", "42", "chat", location=Location(45.0, 39.0)), self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual(state.step, "await_destination")
        await self.router.handle(NormalizedEvent("max", "4", "TEXT", "42", "chat", text="Санкт-Петербург"), self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual(state.product, "route")
        self.assertIn("destination", state.params)

    async def test_controls_and_recipe_repeat(self):
        await self.router._send_route_card(NormalizedEvent("max", "5", "TEXT", "42", "chat"), self.gateway, self.origin, self.destination, {"lead": 24, "speed": 300, "mode": "simple", "spatial_step": 50})
        for event_id, payload in (
            ("6", encode_callback("route", "lead", 48)),
            ("7", encode_callback("route", "speed", 450)),
            ("8", encode_callback("route", "grid", 100)),
            ("9", encode_callback("route", "mode", "pro")),
            ("10", encode_callback("route", "run")),
        ):
            await self.router.handle(NormalizedEvent("max", event_id, "CALLBACK", "42", "chat", callback_payload=payload, callback_id=event_id), self.gateway)
        self.assertEqual(self.built[-1][2:6], (48, 450, "pro", 100))
        recipe = self.store.latest_for_product("max", "42", "route")
        await self.router.handle(NormalizedEvent("max", "11", "CALLBACK", "42", "chat", callback_payload=encode_callback("recipe", "run", recipe.recipe_id), callback_id="11"), self.gateway)
        self.assertIsNone(self.built[-1][-1])


if __name__ == "__main__": unittest.main()
