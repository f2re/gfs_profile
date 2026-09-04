from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from messenger.webhooks import MessengerWebhookService


class DummyGateway:
    def __init__(self, platform: str):
        self.platform = platform


class DummyRouter:
    async def handle(self, event, gateway):
        return None


class CaptureTasks:
    def __init__(self):
        self.count = 0

    def spawn(self, coro):
        self.count += 1
        coro.close()
        return None

    async def shutdown(self):
        return None


class MessengerWebhookTests(unittest.TestCase):
    def _client(self, service: MessengerWebhookService) -> TestClient:
        app = FastAPI()
        app.include_router(service.api_router())
        return TestClient(app)

    def test_max_secret_and_deduplication(self):
        tasks = CaptureTasks()
        service = MessengerWebhookService(
            DummyRouter(),
            max_gateway=DummyGateway("max"),
            max_secret="secret",
            tasks=tasks,
        )
        client = self._client(service)
        payload = {
            "update_type": "message_created",
            "timestamp": 1000,
            "message": {
                "sender": {"user_id": 42},
                "body": {"mid": "m1", "text": "Москва +24", "attachments": []},
            },
        }
        denied = client.post("/webhooks/max", json=payload, headers={"X-Max-Bot-Api-Secret": "bad"})
        self.assertEqual(denied.status_code, 401)
        accepted = client.post("/webhooks/max", json=payload, headers={"X-Max-Bot-Api-Secret": "secret"})
        duplicate = client.post("/webhooks/max", json=payload, headers={"X-Max-Bot-Api-Secret": "secret"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(tasks.count, 1)

    def test_vk_confirmation_checks_group_and_secret(self):
        service = MessengerWebhookService(
            DummyRouter(),
            vk_group_id="77",
            vk_secret="vk-secret",
            vk_confirmation_code="confirm-code",
            tasks=CaptureTasks(),
        )
        client = self._client(service)
        denied = client.post(
            "/webhooks/vk",
            json={"type": "confirmation", "group_id": 77, "secret": "bad"},
        )
        self.assertEqual(denied.status_code, 403)
        response = client.post(
            "/webhooks/vk",
            json={"type": "confirmation", "group_id": 77, "secret": "vk-secret"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "confirm-code")

    def test_webhook_body_limit_and_invalid_json(self):
        service = MessengerWebhookService(
            DummyRouter(),
            max_gateway=DummyGateway("max"),
            tasks=CaptureTasks(),
        )
        client = self._client(service)
        oversized = client.post(
            "/webhooks/max",
            content=b"{" + b"x" * 1_048_576 + b"}",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(oversized.status_code, 413)
        invalid = client.post(
            "/webhooks/max",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(invalid.status_code, 400)


if __name__ == "__main__":
    unittest.main()
