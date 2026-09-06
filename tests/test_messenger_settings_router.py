from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.contracts import NormalizedEvent, PlatformMessage
from messenger.router import RouterDependencies
from messenger.settings_router import SettingsMessengerRouter, SettingsRecipeStore
from messenger.user_locations import MessengerLocationStore


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
        return PlatformMessage(self.platform, chat_id, "image")

    async def send_file(self, chat_id, path, *, caption="", filename=None):
        return PlatformMessage(self.platform, chat_id, "file")

    async def send_animation(self, chat_id, path, *, caption=""):
        return PlatformMessage(self.platform, chat_id, "animation")

    async def answer_callback(self, event, *, text=None):
        self.calls.append(("answer", text, None))


class SettingsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.locations = MessengerLocationStore(self.path)
        self.recipes = SettingsRecipeStore(self.path, locations=self.locations)
        self.router = SettingsMessengerRouter(
            RouterDependencies(geocode=lambda q, n: [Point()]),
            locations=self.locations,
            recipes=self.recipes,
            progress_interval_seconds=0.01,
        )
        self.gateway = Gateway("max")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def event(self, event_id: str, *, command=None, text=None, callback=None):
        return NormalizedEvent(
            "max",
            event_id,
            "CALLBACK" if callback else "COMMAND",
            "42",
            "chat",
            text=text,
            command=command,
            callback_payload=callback,
            callback_id=event_id if callback else None,
        )

    async def test_settings_shows_active_point_and_recipes(self):
        self.recipes.record_success("max", "42", "profile", {"lead": 24}, Point())
        await self.router.handle(self.event("1", command="settings", text="/settings"), self.gateway)
        text = self.gateway.calls[-1][1]
        keyboard = self.gateway.calls[-1][2]
        self.assertIn("Основная точка: Москва", text)
        self.assertIn("Сохранённых сценариев: 1", text)
        payloads = [button.payload for row in keyboard.rows for button in row if button.payload]
        self.assertIn(encode_callback("settings", "locations"), payloads)
        self.assertIn(encode_callback("settings", "recipes", 0), payloads)

    async def test_location_callback_changes_only_current_platform_active_point(self):
        self.locations.remember("max", "42", Point(55.75, 37.62, "Москва"))
        second = self.locations.remember("max", "42", Point(59.94, 30.31, "Санкт-Петербург"), activate=False)
        self.locations.remember("vk", "42", Point(43.12, 131.89, "Владивосток"))
        await self.router.handle(
            self.event("2", callback=encode_callback("settings", "loc", second.location_id)),
            self.gateway,
        )
        self.assertEqual(self.locations.active("max", "42").label, "Санкт-Петербург")
        self.assertEqual(self.locations.active("vk", "42").label, "Владивосток")

    async def test_recipe_delete_does_not_clear_locations(self):
        recipe = self.recipes.record_success("max", "42", "profile", {"lead": 24}, Point())
        await self.router.handle(
            self.event("3", callback=encode_callback("settings", "del", recipe.recipe_id)),
            self.gateway,
        )
        self.assertIsNone(self.recipes.get("max", "42", recipe.recipe_id))
        self.assertIsNotNone(self.locations.active("max", "42"))

    async def test_clear_all_removes_personal_settings_but_not_other_platform(self):
        self.recipes.record_success("max", "42", "profile", {"lead": 24}, Point())
        self.recipes.record_success("vk", "42", "profile", {"lead": 24}, Point(label="VK Москва"))
        await self.router.handle(
            self.event("4", callback=encode_callback("settings", "clearay")),
            self.gateway,
        )
        self.assertEqual(self.recipes.list("max", "42"), [])
        self.assertEqual(self.locations.recent("max", "42"), [])
        self.assertEqual(len(self.recipes.list("vk", "42")), 1)
        self.assertEqual(self.locations.active("vk", "42").label, "VK Москва")

    async def test_start_contains_settings_button(self):
        await self.router.handle(NormalizedEvent("max", "5", "START", "42", "chat"), self.gateway)
        keyboard = self.gateway.calls[-1][2]
        payloads = [button.payload for row in keyboard.rows for button in row if button.payload]
        self.assertIn(encode_callback("settings", "open"), payloads)


if __name__ == "__main__":
    unittest.main()
