from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import register_messenger_webhooks as reg


class FakeMaxClient:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.subscribed = []

    def list_subscriptions(self):
        return self.existing

    def subscribe(self, url, secret, update_types):
        self.subscribed.append((url, secret, list(update_types)))
        return {"success": True}


class FakeVkClient:
    def __init__(self, existing_server_id=None):
        self.calls = []
        self.existing_server_id = existing_server_id

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "groups.getCallbackConfirmationCode":
            return {"code": "confirm"}
        if method == "groups.getCallbackServers":
            items = [] if self.existing_server_id is None else [{"id": self.existing_server_id, "url": "https://example.test/webhooks/vk"}]
            return {"count": len(items), "items": items}
        if method == "groups.addCallbackServer":
            return {"server_id": 99}
        if method == "groups.setCallbackSettings":
            return 1
        raise AssertionError(method)


class RegistrationTests(unittest.TestCase):
    def test_register_max_updates_subscription(self):
        env = {
            "MAX_BOT_TOKEN": "token",
            "MAX_WEBHOOK_URL": "https://example.test/webhooks/max",
            "MAX_WEBHOOK_SECRET": "abcde_123",
        }
        client = FakeMaxClient([{"url": env["MAX_WEBHOOK_URL"]}])
        with patch.dict(os.environ, env, clear=False):
            result = reg.register_max(probe=False, client=client)
        self.assertIn("обновлена", result.detail)
        self.assertEqual(client.subscribed[0][2], reg.MAX_UPDATE_TYPES)

    def test_register_vk_reuses_existing_server(self):
        env = {
            "VK_BOT_TOKEN": "token",
            "VK_GROUP_ID": "77",
            "VK_CALLBACK_URL": "https://example.test/webhooks/vk",
            "VK_CALLBACK_SECRET": "secret",
            "VK_CONFIRMATION_CODE": "confirm",
            "VK_API_VERSION": "5.199",
        }
        client = FakeVkClient(existing_server_id=12)
        with patch.dict(os.environ, env, clear=False):
            result = reg.register_vk(probe=False, client=client)
        methods = [method for method, _ in client.calls]
        self.assertNotIn("groups.addCallbackServer", methods)
        self.assertIn("groups.setCallbackSettings", methods)
        self.assertIn("обновлён", result.detail)

    def test_vk_confirmation_must_match_api(self):
        env = {
            "VK_BOT_TOKEN": "token",
            "VK_GROUP_ID": "77",
            "VK_CALLBACK_URL": "https://example.test/webhooks/vk",
            "VK_CALLBACK_SECRET": "secret",
            "VK_CONFIRMATION_CODE": "wrong",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(reg.WebhookConfigError):
                reg.register_vk(probe=False, client=FakeVkClient())

    def test_rejects_non_https_urls(self):
        with self.assertRaises(reg.WebhookConfigError):
            reg._https_url("http://localhost/vk", "VK_CALLBACK_URL")


if __name__ == "__main__":
    unittest.main()
