from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, NormalizedEvent, PlatformMessage
from messenger.personal_router import PersonalMessengerRouter
from messenger.router import RouterDependencies
from messenger.user_recipes import UserRecipeStore


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat = lat
        self.lon = lon
        self.label = label
        self.source = "test"


class Parsed:
    location_query = "Москва"
    lead_hour = 24
    run = None
    lead_from_user = True


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
        return PlatformMessage(self.platform, chat_id, "image")

    async def send_file(self, chat_id, path, *, caption="", filename=None):
        return PlatformMessage(self.platform, chat_id, "file")

    async def send_animation(self, chat_id, path, *, caption=""):
        return PlatformMessage(self.platform, chat_id, "animation")

    async def answer_callback(self, event, *, text=None):
        self.calls.append(("answer", text, None))


class PersonalRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.built = []

        def builder(point, lead, run, *, progress_callback=None):
            self.built.append((point.label, lead, run))
            return CommonProductResult("profile", "SUMMARY", [], {"lead": lead})

        self.router = PersonalMessengerRouter(
            RouterDependencies(
                geocode=lambda q, n: [Point()],
                profile_builder=builder,
                profile_parser=lambda raw, default: Parsed(),
                canonical_leads=lambda: [0, 3, 6, 12, 24, 48],
            ),
            recipes=self.store,
            progress_interval_seconds=0.01,
        )
        self.gateway = Gateway("max")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_success_creates_recipe_and_start_shows_quick_action(self):
        event = NormalizedEvent("max", "1", "TEXT", "42", "user:42", text="Москва +24")
        await self.router.handle(event, self.gateway)
        recipe = self.store.latest("max", "42")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.params["lead"], 24)

        start = NormalizedEvent("max", "2", "START", "42", "user:42")
        await self.router.handle(start, self.gateway)
        _, _, keyboard = self.gateway.calls[-1]
        callbacks = [button.payload for row in keyboard.rows for button in row if button.payload]
        self.assertIn(encode_callback("recipe", "run", recipe.recipe_id), callbacks)

    async def test_pinned_recipe_opens_from_profile_and_runs_with_fresh_run(self):
        recipe = self.store.record_success("max", "42", "profile", {"lead": 48}, Point(label="Краснодар"))
        self.store.set_pinned("max", "42", recipe.recipe_id, True)

        open_profile = NormalizedEvent(
            "max",
            "3",
            "CALLBACK",
            "42",
            "user:42",
            callback_payload=encode_callback("product", "open", "profile"),
            callback_id="cb1",
        )
        await self.router.handle(open_profile, self.gateway)
        send_calls = [call for call in self.gateway.calls if call[0] == "send_text"]
        self.assertIn("+48", send_calls[-1][1])

        run = NormalizedEvent(
            "max",
            "4",
            "CALLBACK",
            "42",
            "user:42",
            callback_payload=encode_callback("recipe", "run", recipe.recipe_id),
            callback_id="cb2",
        )
        await self.router.handle(run, self.gateway)
        self.assertEqual(self.built[-1][1:], (48, None))


if __name__ == "__main__":
    unittest.main()
