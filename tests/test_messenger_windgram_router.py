from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage
from messenger.router import RouterDependencies
from messenger.user_recipes import UserRecipeStore
from messenger.windgram_router import WindgramMessengerRouter
from messenger.windgram_service import ParsedWindgramInput


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat = lat
        self.lon = lon
        self.label = label
        self.source = "test"


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


class WindgramRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.points = [Point()]
        self.built = []
        self.parsed = ParsedWindgramInput("Москва", None, 0, 120, 6, 500, "wind")

        def profile_builder(point, lead, run, *, progress_callback=None):
            return CommonProductResult("profile", "PROFILE", [], {"lead": lead})

        def windgram_builder(point, lead_from, lead_to, step, top, param, run, *, progress_callback=None):
            self.built.append((point.label, lead_from, lead_to, step, top, param, run))
            return CommonProductResult(
                "windgram",
                "WINDGRAM SUMMARY",
                [],
                {"lead_from": lead_from, "lead_to": lead_to, "step": step, "top_hpa": top, "param": param},
            )

        self.router = WindgramMessengerRouter(
            RouterDependencies(
                geocode=lambda q, n: list(self.points),
                profile_builder=profile_builder,
                canonical_leads=lambda: [0, 3, 6, 12, 24, 48, 72, 96, 120, 240, 384],
            ),
            recipes=self.store,
            windgram_builder=windgram_builder,
            windgram_parser=lambda raw: self.parsed,
            progress_interval_seconds=0.01,
        )
        self.gateway = Gateway("max")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_direct_command_runs_common_builder_and_saves_recipe(self):
        self.parsed = ParsedWindgramInput("Москва", None, 0, 240, 12, 500, "temp")
        event = NormalizedEvent("max", "1", "COMMAND", "42", "chat", text="/windgram Москва to=240 step=12 param=temp", command="windgram")
        await self.router.handle(event, self.gateway)
        self.assertEqual(self.built[-1][1:6], (0, 240, 12, 500, "temp"))
        recipe = self.store.latest_for_product("max", "42", "windgram")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.params["to"], 240)
        self.assertEqual(recipe.params["param"], "temp")
        self.assertNotIn("run", recipe.params)
        self.assertTrue(any(call[0] == "edit_text" and "WINDGRAM SUMMARY" in call[1] for call in self.gateway.calls))

    async def test_interactive_point_then_params_callbacks(self):
        start = NormalizedEvent("max", "2", "COMMAND", "42", "chat", text="/windgram", command="windgram")
        await self.router.handle(start, self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual((state.product, state.step), ("windgram", "await_point"))

        city = NormalizedEvent("max", "3", "TEXT", "42", "chat", text="Москва")
        await self.router.handle(city, self.gateway)
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual(state.step, "params")
        self.assertEqual(state.params["to"], 120)

        for event_id, action, value in (("4", "param", "rh"), ("5", "to", 384), ("6", "step", 12)):
            callback = NormalizedEvent(
                "max", event_id, "CALLBACK", "42", "chat",
                callback_payload=encode_callback("windgram", action, value), callback_id=event_id,
            )
            await self.router.handle(callback, self.gateway)

        run = NormalizedEvent(
            "max", "7", "CALLBACK", "42", "chat",
            callback_payload=encode_callback("windgram", "run"), callback_id="7",
        )
        await self.router.handle(run, self.gateway)
        self.assertEqual(self.built[-1][1:6], (0, 384, 12, 500, "rh"))

    async def test_location_opens_same_params_card(self):
        await self.router.handle(
            NormalizedEvent("max", "8", "COMMAND", "42", "chat", text="/windgram", command="windgram"),
            self.gateway,
        )
        await self.router.handle(
            NormalizedEvent("max", "9", "LOCATION", "42", "chat", location=Location(45.0355, 38.9753)),
            self.gateway,
        )
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual((state.product, state.step), ("windgram", "params"))
        self.assertAlmostEqual(state.point.lat, 45.0355)

    async def test_ambiguous_direct_request_preserves_run_and_params(self):
        run = type("Run", (), {"date": "20260905", "cycle": "00"})()
        self.parsed = ParsedWindgramInput("Киров", run, 12, 240, 12, 700, "temp")
        self.points = [Point(58.6, 49.6, "Киров 1"), Point(54.1, 34.3, "Киров 2")]
        await self.router.handle(
            NormalizedEvent("max", "10", "COMMAND", "42", "chat", text="/windgram Киров ...", command="windgram"),
            self.gateway,
        )
        state = self.router.sessions.get("max", "42", "chat")
        self.assertEqual(state.step, "choose_place")
        self.assertIs(state.pending_run, run)
        self.assertEqual(state.params["to"], 240)

        await self.router.handle(
            NormalizedEvent(
                "max", "11", "CALLBACK", "42", "chat",
                callback_payload=encode_callback("windgram", "place", 1), callback_id="11",
            ),
            self.gateway,
        )
        self.assertEqual(self.built[-1], ("Киров 2", 12, 240, 12, 700, "temp", run))

    async def test_pinned_recipe_repeats_with_fresh_run_and_start_shows_product(self):
        recipe = self.store.record_success(
            "max", "42", "windgram",
            {"from": 0, "to": 120, "step": 6, "top": 500, "param": "wind"},
            Point(label="Краснодар"),
        )
        self.store.set_pinned("max", "42", recipe.recipe_id, True)

        await self.router.handle(NormalizedEvent("max", "12", "START", "42", "chat"), self.gateway)
        _, _, keyboard = self.gateway.calls[-1]
        callbacks = [button.payload for row in keyboard.rows for button in row if button.payload]
        self.assertIn(encode_callback("recipe", "run", recipe.recipe_id), callbacks)
        self.assertIn(encode_callback("product", "open", "windgram"), callbacks)

        await self.router.handle(
            NormalizedEvent(
                "max", "13", "CALLBACK", "42", "chat",
                callback_payload=encode_callback("recipe", "run", recipe.recipe_id), callback_id="13",
            ),
            self.gateway,
        )
        self.assertIsNone(self.built[-1][-1])


if __name__ == "__main__":
    unittest.main()
