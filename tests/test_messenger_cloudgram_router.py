from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.cloudgram_router import CloudgramMessengerRouter
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage
from messenger.router import RouterDependencies
from messenger.user_recipes import UserRecipeStore


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat = lat
        self.lon = lon
        self.label = label
        self.source = "test"


class Parsed:
    def __init__(self, query="Москва", run=None, lead_from=0, lead_to=72, step=3, mode="pro"):
        self.location_query = query
        self.run = run
        self.lead_from = lead_from
        self.lead_to = lead_to
        self.step = step
        self.mode = mode


class Gateway:
    def __init__(self, platform="max"):
        self.platform = platform
        self.calls = []
        self.counter = 0

    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        self.counter += 1
        self.calls.append(("send_text", text, keyboard))
        return PlatformMessage(self.platform, chat_id, str(self.counter))

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        self.calls.append(("edit_text", text, keyboard))
        return PlatformMessage(self.platform, chat_id, str(message_id))

    async def send_image(self, chat_id, path, *, caption=""):
        self.calls.append(("send_image", str(path), caption))
        return PlatformMessage(self.platform, chat_id, "image")

    async def send_file(self, chat_id, path, *, caption="", filename=None):
        self.calls.append(("send_file", str(path), caption))
        return PlatformMessage(self.platform, chat_id, "file")

    async def send_animation(self, chat_id, path, *, caption=""):
        self.calls.append(("send_animation", str(path), caption))
        return PlatformMessage(self.platform, chat_id, "animation")

    async def answer_callback(self, event, *, text=None):
        self.calls.append(("answer", text, None))


class CloudgramRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.points = [Point()]
        self.parsed = Parsed()
        self.built = []

        def builder(point, lead_from, lead_to, step, mode, run, *, progress_callback=None):
            self.built.append((point.label, lead_from, lead_to, step, mode, run))
            return CommonProductResult("cloudgram", "CLOUDGRAM SUMMARY", [], {"lead_to": lead_to, "mode": mode})

        self.router = CloudgramMessengerRouter(
            RouterDependencies(geocode=lambda q, n: list(self.points)),
            recipes=self.store,
            cloudgram_builder=builder,
            cloudgram_parser=lambda raw: self.parsed,
            progress_interval_seconds=0.01,
        )
        self.gateway = Gateway("max")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_direct_command_runs_common_builder_and_saves_recipe(self):
        self.parsed = Parsed("Москва", None, 0, 120, 6, "simple")
        event = NormalizedEvent("max", "1", "COMMAND", "42", "chat", text="/cloudgram Москва to=120 step=6 mode=simple", command="cloudgram")
        await self.router.handle(event, self.gateway)
        self.assertEqual(self.built[-1][:5], ("Москва", 0, 120, 6, "simple"))
        recipe = self.store.latest_for_product("max", "42", "cloudgram")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.params["to"], 120)
        self.assertEqual(recipe.params["mode"], "simple")
        self.assertNotIn("run", recipe.params)
        self.assertTrue(any(call[0] == "edit_text" and "CLOUDGRAM SUMMARY" in call[1] for call in self.gateway.calls))

    async def test_location_and_parameter_callbacks_keep_cloudgram_flow(self):
        start = NormalizedEvent("max", "2", "COMMAND", "42", "chat", text="/cloudgram", command="cloudgram")
        await self.router.handle(start, self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual((state.product, state.step), ("cloudgram", "await_point"))

        location = NormalizedEvent("max", "3", "LOCATION", "42", "chat", location=Location(45.0355, 38.9753))
        await self.router.handle(location, self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual((state.product, state.step), ("cloudgram", "params"))

        for event_id, payload in (
            ("4", encode_callback("cloudgram", "mode", "simple")),
            ("5", encode_callback("cloudgram", "to", 120)),
            ("6", encode_callback("cloudgram", "step", 6)),
        ):
            await self.router.handle(
                NormalizedEvent("max", event_id, "CALLBACK", "42", "chat", callback_payload=payload, callback_id=event_id),
                self.gateway,
            )
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual((state.params["mode"], state.params["to"], state.params["step"]), ("simple", 120, 6))

        await self.router.handle(
            NormalizedEvent("max", "7", "CALLBACK", "42", "chat", callback_payload=encode_callback("cloudgram", "run"), callback_id="7"),
            self.gateway,
        )
        self.assertEqual(self.built[-1][1:5], (0, 120, 6, "simple"))

    async def test_pinned_recipe_repeat_uses_no_saved_run(self):
        recipe = self.store.record_success("max", "42", "cloudgram", {"from": 0, "to": 72, "step": 3, "mode": "pro"}, Point(label="Краснодар"))
        self.store.set_pinned("max", "42", recipe.recipe_id, True)
        event = NormalizedEvent("max", "8", "CALLBACK", "42", "chat", callback_payload=encode_callback("recipe", "run", recipe.recipe_id), callback_id="8")
        await self.router.handle(event, self.gateway)
        self.assertEqual(self.built[-1], ("Краснодар", 0, 72, 3, "pro", None))


if __name__ == "__main__":
    unittest.main()
