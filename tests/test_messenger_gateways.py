from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from messenger.callback_codec import CallbackCodecError, decode_callback, encode_callback
from messenger.contracts import CommonProductResult, Location, NormalizedEvent, PlatformMessage, UiButton, UiKeyboard
from messenger.max.adapter import normalize_max_update
from messenger.max.client import MaxApiClient
from messenger.max.gateway import _keyboard_attachment
from messenger.router import MessengerRouter, RouterDependencies
from messenger.vk.adapter import normalize_vk_update
from messenger.vk.client import VkApiClient
from messenger.vk.gateway import _keyboard_json


@dataclass
class Point:
    lat: float
    lon: float
    label: str
    source: str = "test"


class FakeParsed:
    def __init__(self, query: str, lead: int = 24, explicit: bool = False):
        self.location_query = query
        self.lead_hour = lead
        self.run = None
        self.lead_from_user = explicit


class FakeGateway:
    def __init__(self, platform: str):
        self.platform = platform
        self.calls = []
        self.counter = 0

    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        self.counter += 1
        self.calls.append(("send_text", chat_id, text, keyboard))
        return PlatformMessage(self.platform, chat_id, str(self.counter))

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None):
        self.calls.append(("edit_text", chat_id, message_id, text, keyboard))
        return PlatformMessage(self.platform, chat_id, str(message_id))

    async def send_image(self, chat_id, path, *, caption=""):
        self.calls.append(("send_image", chat_id, str(path), caption))
        return PlatformMessage(self.platform, chat_id, "img")

    async def send_file(self, chat_id, path, *, caption="", filename=None):
        self.calls.append(("send_file", chat_id, str(path), caption, filename))
        return PlatformMessage(self.platform, chat_id, "file")

    async def send_animation(self, chat_id, path, *, caption=""):
        self.calls.append(("send_animation", chat_id, str(path), caption))
        return PlatformMessage(self.platform, chat_id, "anim")

    async def answer_callback(self, event, *, text=None):
        self.calls.append(("answer_callback", event.callback_id, text))


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None, text=""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses=None, upload_response=None):
        self.responses = list(responses or [])
        self.upload_response = upload_response or FakeResponse({"token": "uploaded-token"})
        self.calls = []
        self.upload_calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        if url.startswith("https://upload"):
            self.upload_calls.append((url, kwargs))
            return self.upload_response
        self.calls.append(("POST", url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected post")
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def test_max_uses_authorization_header_and_user_recipient(self):
        session = FakeSession([FakeResponse({"message": {"body": {"mid": "m1"}}})])
        client = MaxApiClient("secret-token", session=session, retries=0)
        client.send_message("user:42", {"text": "hi"})
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://platform-api2.max.ru/messages"))
        self.assertEqual(kwargs["headers"]["Authorization"], "secret-token")
        self.assertEqual(kwargs["params"], {"user_id": 42})
        self.assertNotIn("access_token", kwargs["params"])

    def test_max_upload_normalizes_payload_to_token(self):
        session = FakeSession(upload_response=FakeResponse({"retval": 1}))
        client = MaxApiClient("token", session=session, retries=0)
        client.create_upload = lambda media_type: {"url": "https://upload.example/file", "token": "slot-token"}
        with tempfile.NamedTemporaryFile() as item:
            payload = client.upload(Path(item.name), "video")
        self.assertEqual(payload, {"token": "slot-token"})
        self.assertEqual(len(session.upload_calls), 1)

    def test_vk_send_has_version_token_and_random_id(self):
        session = FakeSession([FakeResponse({"response": 77})])
        client = VkApiClient("vk-token", session=session, retries=0)
        self.assertEqual(client.send_message("42", "hi"), 77)
        _, url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api.vk.com/method/messages.send")
        data = kwargs["data"]
        self.assertEqual(data["access_token"], "vk-token")
        self.assertEqual(data["v"], "5.199")
        self.assertGreater(data["random_id"], 0)


class CallbackCodecTests(unittest.TestCase):
    def test_roundtrip(self):
        payload = encode_callback("profile", "lead", 24)
        self.assertLessEqual(len(payload.encode()), 64)
        decoded = decode_callback(payload)
        self.assertEqual((decoded.scope, decoded.action, decoded.value), ("profile", "lead", "24"))

    def test_rejects_oversize(self):
        with self.assertRaises(CallbackCodecError):
            encode_callback("x" * 70, "a")


class AdapterTests(unittest.TestCase):
    def test_max_message(self):
        update = {
            "update_type": "message_created",
            "timestamp": 1000,
            "message": {
                "sender": {"user_id": 42},
                "recipient": {"chat_id": 9, "chat_type": "dialog"},
                "body": {"mid": "m1", "text": "/profile Москва +24", "attachments": []},
            },
        }
        event = normalize_max_update(update)
        self.assertEqual(event.platform, "max")
        self.assertEqual(event.user_id, "42")
        self.assertEqual(event.chat_id, "user:42")
        self.assertEqual(event.command, "profile")

    def test_max_location(self):
        update = {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": 42},
                "body": {"mid": "m2", "attachments": [{"type": "location", "payload": {"latitude": 55.7, "longitude": 37.6}}]},
            },
        }
        event = normalize_max_update(update)
        self.assertEqual(event.event_type, "LOCATION")
        self.assertEqual(event.location, Location(55.7, 37.6))

    def test_max_callback(self):
        update = {
            "update_type": "message_callback",
            "timestamp": 1000,
            "user": {"user_id": 42},
            "callback": {"callback_id": "cb", "payload": "v1|profile|lead|24"},
            "message": {"body": {"mid": "m1"}},
        }
        event = normalize_max_update(update)
        self.assertEqual(event.callback_id, "cb")
        self.assertEqual(event.callback_payload, "v1|profile|lead|24")

    def test_vk_message_geo(self):
        update = {
            "type": "message_new",
            "event_id": "ev",
            "group_id": 5,
            "object": {"message": {"id": 7, "from_id": 42, "peer_id": 42, "text": "", "date": 10, "geo": {"coordinates": {"latitude": 55.7, "longitude": 37.6}}}},
        }
        event = normalize_vk_update(update)
        self.assertEqual(event.event_type, "LOCATION")
        self.assertEqual(event.location, Location(55.7, 37.6))

    def test_vk_callback_payload(self):
        update = {
            "type": "message_event",
            "event_id": "outer",
            "object": {"event_id": "inner", "user_id": 42, "peer_id": 42, "payload": {"p": "v1|profile|lead|24"}},
        }
        event = normalize_vk_update(update)
        self.assertEqual(event.callback_id, "inner")
        self.assertEqual(event.callback_payload, "v1|profile|lead|24")


class KeyboardTests(unittest.TestCase):
    def test_max_keyboard(self):
        keyboard = UiKeyboard.from_rows([[UiButton("+24", "callback", "x"), UiButton("geo", "request_location")]])
        attachment = _keyboard_attachment(keyboard)
        buttons = attachment["payload"]["buttons"][0]
        self.assertEqual(buttons[0]["type"], "callback")
        self.assertEqual(buttons[0]["payload"], "x")
        self.assertEqual(buttons[1]["type"], "request_geo_location")

    def test_vk_keyboard(self):
        keyboard = UiKeyboard.from_rows([[UiButton("+24", "callback", "x"), UiButton("geo", "request_location")]])
        value = _keyboard_json(keyboard)
        self.assertIn('"type":"callback"', value)
        self.assertIn('"type":"location"', value)


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_city_explicit_lead_runs_one_common_profile(self):
        point = Point(55.7, 37.6, "Москва")
        built = []

        def parser(raw, default):
            return FakeParsed("Москва", 24, True)

        def geocode(query, limit):
            return [point]

        def builder(point_arg, lead, run, *, progress_callback=None):
            built.append((point_arg, lead, run))
            return CommonProductResult("profile", "SUMMARY", [], {"lead": lead}, repeat_command="/profile 1 2 +24")

        router = MessengerRouter(
            RouterDependencies(geocode=geocode, profile_builder=builder, profile_parser=parser, canonical_leads=lambda: [0, 3, 6, 12, 24, 48]),
            progress_interval_seconds=0.01,
        )
        gateway = FakeGateway("max")
        event = NormalizedEvent("max", "1", "TEXT", "42", "user:42", text="Москва +24")
        await router.handle(event, gateway)
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0][1], 24)
        self.assertTrue(any(call[0] == "edit_text" and "SUMMARY" in call[3] for call in gateway.calls))

    async def test_ambiguous_city_then_place_then_lead(self):
        points = [Point(1, 2, "Киров 1"), Point(3, 4, "Киров 2")]

        def parser(raw, default):
            return FakeParsed("Киров", 24, False)

        router = MessengerRouter(
            RouterDependencies(geocode=lambda q, n: points, profile_builder=lambda *a, **k: None, profile_parser=parser, canonical_leads=lambda: [0, 3, 6, 12, 24, 48]),
            progress_interval_seconds=0.01,
        )
        gateway = FakeGateway("vk")
        event = NormalizedEvent("vk", "1", "TEXT", "42", "42", text="Киров")
        await router.handle(event, gateway)
        state = router.sessions.get("vk", "42", "42")
        self.assertEqual(state.step, "choose_place")

        cb = NormalizedEvent("vk", "2", "CALLBACK", "42", "42", callback_payload=encode_callback("profile", "place", 1), callback_id="cb")
        await router.handle(cb, gateway)
        state = router.sessions.get("vk", "42", "42")
        self.assertEqual(state.point.label, "Киров 2")
        self.assertEqual(state.step, "choose_lead")


class WebhookUtilityTests(unittest.TestCase):
    def test_deduplicator(self):
        from messenger.webhooks import EventDeduplicator
        dedupe = EventDeduplicator(ttl_seconds=60)
        self.assertTrue(dedupe.accept("max", "1"))
        self.assertFalse(dedupe.accept("max", "1"))
        self.assertTrue(dedupe.accept("vk", "1"))


if __name__ == "__main__":
    unittest.main()
