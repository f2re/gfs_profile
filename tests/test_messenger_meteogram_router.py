from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage
from messenger.meteogram_router import MeteogramMessengerRouter
from messenger.router import RouterDependencies
from messenger.user_recipes import UserRecipeStore


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat, self.lon, self.label, self.source = lat, lon, label, "test"


class Parsed:
    def __init__(self, query="Москва", source_id="gfs", days=5, output_format="png"):
        self.location_query = query
        self.source_id = source_id
        self.days = days
        self.output_format = output_format


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


class MeteogramRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.points = [Point()]
        self.built = []

        def builder(point, source_id, days, output_format, *, progress_callback=None):
            self.built.append((point.label, source_id, days, output_format))
            return CommonProductResult("meteogram", "METEO SUMMARY", [], {"cycle": None})

        self.router = MeteogramMessengerRouter(
            RouterDependencies(geocode=lambda q, n: list(self.points)),
            recipes=self.store,
            meteogram_builder=builder,
            meteogram_parser=lambda raw: Parsed(),
            progress_interval_seconds=0.01,
        )
        self.router.meteogram_semaphore = self.router.gfs_semaphore
        self.gateway = Gateway("max")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_direct_command_runs_common_builder_and_saves_recipe(self):
        event = NormalizedEvent("max", "1", "COMMAND", "42", "chat", text="/meteogram Москва", command="meteogram")
        await self.router.handle(event, self.gateway)
        self.assertEqual(self.built[-1], ("Москва", "gfs", 5, "png"))
        recipe = self.store.latest_for_product("max", "42", "meteogram")
        self.assertEqual(recipe.params, {"source": "gfs", "days": 5, "format": "png"})
        self.assertNotIn("run", recipe.params)

    async def test_geo_then_change_to_ensemble_pdf(self):
        await self.router.handle(NormalizedEvent("max", "2", "COMMAND", "42", "chat", text="/meteogram", command="meteogram"), self.gateway)
        await self.router.handle(NormalizedEvent("max", "3", "LOCATION", "42", "chat", location=Location(45.0, 39.0)), self.gateway)
        await self.router.handle(NormalizedEvent("max", "4", "CALLBACK", "42", "chat", callback_payload=encode_callback("meteo", "source", "gefs"), callback_id="4"), self.gateway)
        await self.router.handle(NormalizedEvent("max", "5", "CALLBACK", "42", "chat", callback_payload=encode_callback("meteo", "days", 10), callback_id="5"), self.gateway)
        await self.router.handle(NormalizedEvent("max", "6", "CALLBACK", "42", "chat", callback_payload=encode_callback("meteo", "out", "pdf"), callback_id="6"), self.gateway)
        await self.router.handle(NormalizedEvent("max", "7", "CALLBACK", "42", "chat", callback_payload=encode_callback("meteo", "run"), callback_id="7"), self.gateway)
        self.assertEqual(self.built[-1][1:], ("gefs", 10, "pdf"))

    async def test_meteogram_recipe_does_not_hijack_map_default(self):
        self.store.record_success(
            "max",
            "42",
            "meteogram",
            {"source": "gfs", "days": 5, "format": "png"},
            Point(),
        )
        built_before = len(self.built)
        await self.router.handle(
            NormalizedEvent("max", "8", "COMMAND", "42", "chat", text="/map", command="map"),
            self.gateway,
        )
        self.assertEqual(len(self.built), built_before)
        state = self.router.sessions.get("max", "42", "chat")
        if state is not None:
            self.assertEqual(state.product, "map")
        self.assertFalse(
            any(
                call[0] in {"send_text", "edit_text"} and "METEO SUMMARY" in call[1]
                for call in self.gateway.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
