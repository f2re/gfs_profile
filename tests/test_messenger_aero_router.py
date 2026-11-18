from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.aero_router import AeroMessengerRouter
from messenger.callback_codec import encode_callback
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
    def __init__(self, query="Москва", lead=24, explicit=True, run=None):
        self.location_query = query
        self.lead_hour = lead
        self.lead_from_user = explicit
        self.run = run
        self.diagram_type = "skewt"


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


class AeroRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.points = [Point()]
        self.parsed = Parsed()
        self.aero_built = []
        self.profile_built = []

        def profile_builder(point, lead, run, *, progress_callback=None):
            self.profile_built.append((point.label, lead, run))
            return CommonProductResult("profile", "PROFILE", [], {"lead": lead})

        def aero_builder(point, lead, run, *, progress_callback=None):
            self.aero_built.append((point.label, lead, run))
            return CommonProductResult("aero", "AERO SUMMARY", [], {"lead": lead})

        self.router = AeroMessengerRouter(
            RouterDependencies(
                geocode=lambda q, n: list(self.points),
                profile_builder=profile_builder,
                profile_parser=lambda raw, default: Parsed(raw, 24, True),
                canonical_leads=lambda: [0, 3, 6, 12, 24, 48, 72, 96],
            ),
            recipes=self.store,
            aero_builder=aero_builder,
            aero_parser=lambda raw, default: self.parsed,
            progress_interval_seconds=0.01,
        )
        self.gateway = Gateway("max")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_direct_aero_command_runs_common_builder_and_saves_recipe(self):
        event = NormalizedEvent(
            "max",
            "1",
            "COMMAND",
            "42",
            "user:42",
            text="/aero Москва +24",
            command="aero",
        )
        await self.router.handle(event, self.gateway)
        self.assertEqual(self.aero_built, [("Москва", 24, None)])
        recipe = self.store.latest_for_product("max", "42", "aero")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.params["lead"], 24)
        self.assertEqual(recipe.params["diagram_type"], "skewt")
        self.assertTrue(any(call[0] == "edit_text" and "AERO SUMMARY" in call[1] for call in self.gateway.calls))

    async def test_aero_without_args_then_city_uses_aero_flow_not_profile(self):
        self.parsed = Parsed("Москва", 24, False)
        start = NormalizedEvent(
            "max",
            "2",
            "COMMAND",
            "42",
            "user:42",
            text="/aero",
            command="aero",
        )
        await self.router.handle(start, self.gateway)
        state = self.router.sessions.get("max", "42", "user:42")
        self.assertEqual((state.product, state.step), ("aero", "await_point"))

        city = NormalizedEvent("max", "3", "TEXT", "42", "user:42", text="Москва")
        await self.router.handle(city, self.gateway)
        state = self.router.sessions.get("max", "42", "user:42")
        self.assertEqual((state.product, state.step), ("aero", "choose_lead"))
        self.assertFalse(self.profile_built)
        _, _, keyboard = self.gateway.calls[-1]
        callbacks = [button.payload for row in keyboard.rows for button in row if button.payload]
        self.assertIn(encode_callback("aero", "lead", 24), callbacks)

    async def test_ambiguous_aero_city_preserves_explicit_run_and_lead(self):
        run = type("Run", (), {"date": "20260904", "cycle": "06"})()
        self.parsed = Parsed("Киров", 48, True, run)
        self.points = [Point(58.6, 49.6, "Киров 1"), Point(54.1, 34.3, "Киров 2")]
        event = NormalizedEvent(
            "max",
            "4",
            "COMMAND",
            "42",
            "user:42",
            text="/aero Киров run=20260904/06 +48",
            command="aero",
        )
        await self.router.handle(event, self.gateway)
        state = self.router.sessions.get("max", "42", "user:42")
        self.assertEqual(state.step, "choose_place")
        self.assertEqual(state.pending_lead, 48)
        self.assertIs(state.pending_run, run)

        callback = NormalizedEvent(
            "max",
            "5",
            "CALLBACK",
            "42",
            "user:42",
            callback_payload=encode_callback("aero", "place", 1),
            callback_id="cb",
        )
        await self.router.handle(callback, self.gateway)
        self.assertEqual(self.aero_built[-1], ("Киров 2", 48, run))

    async def test_aero_location_then_lead(self):
        self.parsed = Parsed("", 24, False)
        start = NormalizedEvent(
            "max",
            "6",
            "COMMAND",
            "42",
            "user:42",
            text="/aero",
            command="aero",
        )
        await self.router.handle(start, self.gateway)
        location = NormalizedEvent(
            "max",
            "7",
            "LOCATION",
            "42",
            "user:42",
            location=Location(45.0355, 38.9753),
        )
        await self.router.handle(location, self.gateway)
        state = self.router.sessions.get("max", "42", "user:42")
        self.assertEqual(state.product, "aero")
        self.assertEqual(state.step, "choose_lead")

        lead = NormalizedEvent(
            "max",
            "8",
            "CALLBACK",
            "42",
            "user:42",
            callback_payload=encode_callback("aero", "lead", 24),
            callback_id="cb2",
        )
        await self.router.handle(lead, self.gateway)
        self.assertEqual(self.aero_built[-1][1:], (24, None))

    async def test_pinned_aero_recipe_opens_and_repeat_uses_fresh_run(self):
        recipe = self.store.record_success(
            "max",
            "42",
            "aero",
            {"lead": 72, "diagram_type": "skewt"},
            Point(label="Краснодар"),
        )
        self.store.set_pinned("max", "42", recipe.recipe_id, True)
        open_aero = NormalizedEvent(
            "max",
            "9",
            "CALLBACK",
            "42",
            "user:42",
            callback_payload=encode_callback("product", "open", "aero"),
            callback_id="cb3",
        )
        await self.router.handle(open_aero, self.gateway)
        send_calls = [call for call in self.gateway.calls if call[0] == "send_text"]
        self.assertIn("+72", send_calls[-1][1])

        run_recipe = NormalizedEvent(
            "max",
            "10",
            "CALLBACK",
            "42",
            "user:42",
            callback_payload=encode_callback("recipe", "run", recipe.recipe_id),
            callback_id="cb4",
        )
        await self.router.handle(run_recipe, self.gateway)
        self.assertEqual(self.aero_built[-1][1:], (72, None))

    async def test_start_can_show_profile_and_aero_recipes(self):
        profile = self.store.record_success("max", "42", "profile", {"lead": 24}, Point())
        aero = self.store.record_success("max", "42", "aero", {"lead": 48}, Point(label="Краснодар"))
        self.store.set_pinned("max", "42", aero.recipe_id, True)
        start = NormalizedEvent("max", "11", "START", "42", "user:42")
        await self.router.handle(start, self.gateway)
        _, _, keyboard = self.gateway.calls[-1]
        callbacks = [button.payload for row in keyboard.rows for button in row if button.payload]
        self.assertIn(encode_callback("recipe", "run", aero.recipe_id), callbacks)
        self.assertIn(encode_callback("product", "open", "profile"), callbacks)
        self.assertIn(encode_callback("product", "open", "aero"), callbacks)
        self.assertTrue(profile.recipe_id > 0)


if __name__ == "__main__":
    unittest.main()
