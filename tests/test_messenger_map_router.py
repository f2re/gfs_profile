from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage
from messenger.map_router import MapMessengerRouter
from messenger.router import RouterDependencies
from messenger.user_recipes import UserRecipeStore


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat, self.lon, self.label, self.source = lat, lon, label, "test"


class Parsed:
    def __init__(self, query="Москва", run=None, lead_from=0, lead_to=48, step=3, mode="gif", radius_km=100, basemap="places"):
        self.location_query = query
        self.run = run
        self.lead_from = lead_from
        self.lead_to = lead_to
        self.step = step
        self.mode = mode
        self.radius_km = radius_km
        self.basemap = basemap


class Gateway:
    def __init__(self, platform="max"):
        self.platform = platform; self.calls = []; self.counter = 0
    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        self.counter += 1; self.calls.append(("send_text", text, keyboard)); return PlatformMessage(self.platform, chat_id, str(self.counter))
    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        self.calls.append(("edit_text", text, keyboard)); return PlatformMessage(self.platform, chat_id, str(message_id))
    async def send_image(self, chat_id, path, *, caption=""):
        self.calls.append(("send_image", str(path), caption)); return PlatformMessage(self.platform, chat_id, "image")
    async def send_file(self, chat_id, path, *, caption="", filename=None):
        self.calls.append(("send_file", str(path), caption)); return PlatformMessage(self.platform, chat_id, "file")
    async def send_animation(self, chat_id, path, *, caption=""):
        self.calls.append(("send_animation", str(path), caption)); return PlatformMessage(self.platform, chat_id, "animation")
    async def answer_callback(self, event, *, text=None):
        self.calls.append(("answer", text, None))


class MapRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.points = [Point()]
        self.parsed = Parsed()
        self.built = []
        def builder(point, lead_from, lead_to, step, mode, radius, basemap, run, *, progress_callback=None):
            self.built.append((point.label, lead_from, lead_to, step, mode, int(radius), basemap, run))
            return CommonProductResult("map", "MAP SUMMARY", [], {"mode": mode, "lead_to": lead_to})
        self.router = MapMessengerRouter(
            RouterDependencies(geocode=lambda q, n: list(self.points)),
            recipes=self.store,
            map_builder=builder,
            map_parser=lambda raw: self.parsed,
            progress_interval_seconds=0.01,
        )
        self.gateway = Gateway("max")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_direct_command_runs_common_builder_and_saves_recipe(self):
        event = NormalizedEvent("max", "1", "COMMAND", "42", "chat", text="/map Москва", command="map")
        await self.router.handle(event, self.gateway)
        self.assertEqual(self.built[-1][:7], ("Москва", 0, 48, 3, "gif", 100, "places"))
        recipe = self.store.latest_for_product("max", "42", "map")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.params["mode"], "gif")
        self.assertNotIn("run", recipe.params)

    async def test_geo_and_controls_keep_map_state(self):
        await self.router.handle(NormalizedEvent("max", "2", "COMMAND", "42", "chat", text="/map", command="map"), self.gateway)
        await self.router.handle(NormalizedEvent("max", "3", "LOCATION", "42", "chat", location=Location(45.0355, 38.9753)), self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual((state.product, state.step), ("map", "params"))
        for event_id, payload in (
            ("4", encode_callback("map", "to", 96)),
            ("5", encode_callback("map", "radius", 50)),
            ("6", encode_callback("map", "base", "roads")),
        ):
            await self.router.handle(NormalizedEvent("max", event_id, "CALLBACK", "42", "chat", callback_payload=payload, callback_id=event_id), self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual(state.params["to"], 96)
        self.assertEqual(state.params["step"], 6)  # auto-adjusted to <=18 animation frames
        self.assertEqual(int(state.params["radius"]), 50)
        self.assertEqual(state.params["basemap"], "roads")
        await self.router.handle(NormalizedEvent("max", "7", "CALLBACK", "42", "chat", callback_payload=encode_callback("map", "run"), callback_id="7"), self.gateway)
        self.assertEqual(self.built[-1][1:7], (0, 96, 6, "gif", 50, "roads"))

    async def test_recipe_repeat_uses_no_saved_run(self):
        recipe = self.store.record_success("max", "42", "map", {"from": 0, "to": 48, "step": 3, "mode": "gif", "radius": 100, "basemap": "places"}, Point(label="Краснодар"))
        await self.router.handle(NormalizedEvent("max", "8", "CALLBACK", "42", "chat", callback_payload=encode_callback("recipe", "run", recipe.recipe_id), callback_id="8"), self.gateway)
        self.assertIsNone(self.built[-1][-1])


if __name__ == "__main__":
    unittest.main()
