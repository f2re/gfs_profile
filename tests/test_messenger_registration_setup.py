from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_messenger_config import prepare_environment, read_env_file
from register_messenger_webhooks import status_max, status_vk


class _MaxClient:
    def __init__(self, items):
        self.items = items

    def list_subscriptions(self):
        return self.items


class _VkClient:
    def __init__(self, url: str, *, message_event: int = 1):
        self.url = url
        self.message_event = message_event

    def call(self, method: str, **params):
        if method == "groups.getCallbackConfirmationCode":
            return {"code": "vk-code"}
        if method == "groups.getCallbackServers":
            return {"items": [{"id": 17, "url": self.url}]}
        if method == "groups.getCallbackSettings":
            return {"events": {"message_new": 1, "message_event": self.message_event}}
        raise AssertionError(method)


class MessengerRegistrationSetupTests(unittest.TestCase):
    def test_prepare_generates_secrets_and_fetches_vk_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "MAX_BOT_TOKEN=max-token\n"
                "MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max\n"
                "MAX_WEBHOOK_SECRET=\n"
                "VK_BOT_TOKEN=vk-token\n"
                "VK_GROUP_ID=12345\n"
                "VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk\n"
                "VK_CALLBACK_SECRET=\n"
                "VK_CONFIRMATION_CODE=\n"
                "VK_API_VERSION=5.199\n",
                encoding="utf-8",
            )
            with patch("prepare_messenger_config._vk_confirmation_code", return_value="vk-code"):
                changed = prepare_environment(env_file)
            values = read_env_file(env_file)
            self.assertTrue(values["MAX_WEBHOOK_SECRET"])
            self.assertTrue(values["VK_CALLBACK_SECRET"])
            self.assertEqual(values["VK_CONFIRMATION_CODE"], "vk-code")
            self.assertIn("MAX_WEBHOOK_SECRET generated", changed)
            self.assertIn("VK_CONFIRMATION_CODE fetched", changed)

    def test_prepare_keeps_secrets_and_refreshes_stale_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "MAX_BOT_TOKEN=max-token\n"
                "MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max\n"
                "MAX_WEBHOOK_SECRET=existing-max\n"
                "VK_BOT_TOKEN=vk-token\n"
                "VK_GROUP_ID=12345\n"
                "VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk\n"
                "VK_CALLBACK_SECRET=existing-vk\n"
                "VK_CONFIRMATION_CODE=stale-code\n"
                "VK_API_VERSION=5.199\n",
                encoding="utf-8",
            )
            with patch("prepare_messenger_config._vk_confirmation_code", return_value="vk-code"):
                changed = prepare_environment(env_file)
            values = read_env_file(env_file)
            self.assertEqual(values["MAX_WEBHOOK_SECRET"], "existing-max")
            self.assertEqual(values["VK_CALLBACK_SECRET"], "existing-vk")
            self.assertEqual(values["VK_CONFIRMATION_CODE"], "vk-code")
            self.assertEqual(changed, ["VK_CONFIRMATION_CODE refreshed"])

    def test_status_reports_real_max_subscription(self) -> None:
        env = {
            "MAX_BOT_TOKEN": "token",
            "MAX_WEBHOOK_URL": "https://bot.example.ru/webhooks/max",
            "MAX_WEBHOOK_SECRET": "secret_123",
        }
        client = _MaxClient([
            {
                "url": env["MAX_WEBHOOK_URL"],
                "update_types": ["bot_started", "message_created", "message_callback"],
            }
        ])
        with patch.dict(os.environ, env, clear=True):
            result = status_max(client=client)
        self.assertTrue(result.ok)
        self.assertIn("активна", result.detail)

    def test_status_reports_vk_callback_events(self) -> None:
        url = "https://bot.example.ru/webhooks/vk"
        env = {
            "VK_BOT_TOKEN": "token",
            "VK_GROUP_ID": "12345",
            "VK_CALLBACK_URL": url,
            "VK_CALLBACK_SECRET": "secret",
            "VK_CONFIRMATION_CODE": "vk-code",
            "VK_API_VERSION": "5.199",
        }
        with patch.dict(os.environ, env, clear=True):
            good = status_vk(client=_VkClient(url))
            bad = status_vk(client=_VkClient(url, message_event=0))
        self.assertTrue(good.ok)
        self.assertFalse(bad.ok)
        self.assertIn("message_event", bad.detail)


if __name__ == "__main__":
    unittest.main()
