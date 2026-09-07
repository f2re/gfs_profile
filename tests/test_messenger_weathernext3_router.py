from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, NormalizedEvent, PlatformMessage
from messenger.router import RouterDependencies
from messenger.user_recipes import UserRecipeStore
from messenger.weathernext3_router import WeatherNext3MessengerRouter


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat, self.lon, self.label, self.source = lat, lon, label, "test"


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


class WeatherNext3RouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.built = []

        def builder(point, kind, *, progress_callback=None, **params):
            self.built.append((point.label, kind, dict(params)))
            return CommonProductResult(
                "weathernext3",
                f"WN3 {kind}",
                [],
                {"model": "WeatherNext 3", "kind": kind, "data_kind": "model"},
            )

        self.router = WeatherNext3MessengerRouter(
            RouterDependencies(geocode=lambda query, limit: [Point()]),
            recipes=self.store,
            wn3_builder=builder,
            progress_interval_seconds=0.001,
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_start_exposes_weathernext3_on_max_and_vk(self):
        for platform in ("max", "vk"):
            gateway = Gateway(platform)
            event = NormalizedEvent(platform, f"start-{platform}", "COMMAND", "42", "chat", text="/start", command="start")
            await self.router.handle(event, gateway)
            keyboards = [call[2] for call in gateway.calls if call[0] == "send_text" and call[2] is not None]
            labels = [button.text for keyboard in keyboards for row in keyboard.rows for button in row]
            self.assertIn("🛰 WeatherNext 3", labels)

    async def test_direct_point_forecast_uses_common_builder(self):
        gateway = Gateway("max")
        event = NormalizedEvent("max", "1", "COMMAND", "42", "chat", text="/wn3 Москва +24", command="wn3")
        await self.router.handle(event, gateway)
        self.assertEqual(self.built[-1][0:2], ("Москва", "point"))
        self.assertEqual(self.built[-1][2]["hours"], 24)
        self.assertTrue(any(call[0] == "edit_text" and "WN3 point" in call[1] for call in gateway.calls))

    async def test_direct_animation_keeps_wn3_parameters(self):
        gateway = Gateway("vk")
        event = NormalizedEvent(
            "vk", "2", "COMMAND", "42", "chat",
            text="/wn3 Москва kind=clouds to=24 step=3 radius=100 mode=animation",
            command="wn3",
        )
        await self.router.handle(event, gateway)
        _, kind, params = self.built[-1]
        self.assertEqual(kind, "clouds")
        self.assertEqual((params["from"], params["to"], params["step"], int(params["radius"])), (1, 24, 3, 100))
        self.assertNotIn("kind", params)

    async def test_single_map_lead_is_explicit(self):
        gateway = Gateway("max")
        await self.router.handle(
            NormalizedEvent("max", "3", "COMMAND", "42", "chat", text="/wn3", command="wn3"),
            gateway,
        )
        # No saved location: enter a point first.
        await self.router.handle(
            NormalizedEvent("max", "4", "TEXT", "42", "chat", text="Москва"), gateway,
        )
        await self.router.handle(
            NormalizedEvent("max", "5", "CALLBACK", "42", "chat", callback_payload=encode_callback("wn3", "kind", "clouds"), callback_id="5"),
            gateway,
        )
        await self.router.handle(
            NormalizedEvent("max", "6", "CALLBACK", "42", "chat", callback_payload=encode_callback("wn3", "mode", "single"), callback_id="6"),
            gateway,
        )
        await self.router.handle(
            NormalizedEvent("max", "7", "CALLBACK", "42", "chat", callback_payload=encode_callback("wn3", "lead", 24), callback_id="7"),
            gateway,
        )
        await self.router.handle(
            NormalizedEvent("max", "8", "CALLBACK", "42", "chat", callback_payload=encode_callback("wn3", "run"), callback_id="8"),
            gateway,
        )
        _, kind, params = self.built[-1]
        self.assertEqual(kind, "clouds")
        self.assertEqual((params["mode"], params["from"], params["to"]), ("single", 24, 24))


if __name__ == "__main__":
    unittest.main()
