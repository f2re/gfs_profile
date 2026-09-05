from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from messenger_config_check import ConfigError, validate_environment


class MessengerConfigCheckTests(unittest.TestCase):
    def test_telegram_only_common_runtime_is_valid(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "MESSENGER_RUNTIME_ENABLED": "1",
            "MESSENGER_RUNTIME_PORT": "8081",
            "MAX_BOT_TOKEN": "",
            "VK_BOT_TOKEN": "",
        }
        with patch.dict(os.environ, env, clear=True):
            state = validate_environment()
        self.assertTrue(state["runtime_enabled"])
        self.assertTrue(state["telegram"])
        self.assertFalse(state["max"])
        self.assertFalse(state["vk"])

    def test_broken_vk_does_not_block_ready_telegram_and_max(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "MAX_BOT_TOKEN": "max-token",
            "MAX_WEBHOOK_URL": "https://bot.example.test/webhooks/max",
            "MAX_WEBHOOK_SECRET": "secret_123",
            "VK_BOT_TOKEN": "vk-token",
            "VK_GROUP_ID": "bad-id",
            "VK_CALLBACK_URL": "https://bot.example.test/webhooks/vk",
            "MESSENGER_RUNTIME_PORT": "8081",
        }
        with patch.dict(os.environ, env, clear=True):
            state = validate_environment()
        self.assertTrue(state["telegram"])
        self.assertTrue(state["max"])
        self.assertFalse(state["vk"])
        self.assertEqual(state["platform_status"]["vk"]["state"], "degraded")

    def test_max_token_requires_https_and_secret_only_for_max(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "MAX_BOT_TOKEN": "max-token",
            "MAX_WEBHOOK_URL": "http://example.test/webhooks/max",
            "MAX_WEBHOOK_SECRET": "secret_123",
        }
        with patch.dict(os.environ, env, clear=True):
            state = validate_environment()
        self.assertTrue(state["telegram"])
        self.assertFalse(state["max"])
        self.assertEqual(state["platform_status"]["max"]["state"], "degraded")
        with patch.dict(os.environ, env, clear=True), self.assertRaises(ConfigError):
            validate_environment(strict_platforms={"max"})

    def test_max_rejects_non_443_webhook_port_without_blocking_runtime(self) -> None:
        env = {
            "MAX_BOT_TOKEN": "max-token",
            "MAX_WEBHOOK_URL": "https://bot.example.test:8443/webhooks/max",
            "MAX_WEBHOOK_SECRET": "secret_123",
        }
        with patch.dict(os.environ, env, clear=True):
            state = validate_environment()
        self.assertFalse(state["max"])
        self.assertIn("443", state["platform_status"]["max"]["reason"])

    def test_valid_max_and_vk_configuration(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "MAX_BOT_TOKEN": "max-token",
            "MAX_WEBHOOK_URL": "https://bot.example.test/webhooks/max",
            "MAX_WEBHOOK_SECRET": "secret_123",
            "VK_BOT_TOKEN": "vk-token",
            "VK_GROUP_ID": "12345",
            "VK_CALLBACK_URL": "https://bot.example.test/webhooks/vk",
            "VK_CALLBACK_SECRET": "vk-secret",
            "VK_CONFIRMATION_CODE": "confirm",
            "MESSENGER_RUNTIME_PORT": "8090",
        }
        with patch.dict(os.environ, env, clear=True):
            state = validate_environment()
        self.assertTrue(state["telegram"])
        self.assertTrue(state["max"])
        self.assertTrue(state["vk"])
        self.assertEqual(state["port"], 8090)

    def test_all_messengers_may_be_off_while_web_runtime_remains_valid(self) -> None:
        env = {
            "TELEGRAM_ENABLED": "0",
            "MAX_ENABLED": "0",
            "VK_ENABLED": "0",
            "MESSENGER_RUNTIME_ENABLED": "1",
            "MESSENGER_RUNTIME_PORT": "8081",
        }
        with patch.dict(os.environ, env, clear=True):
            state = validate_environment()
        self.assertFalse(state["telegram"])
        self.assertFalse(state["max"])
        self.assertFalse(state["vk"])
        self.assertEqual(state["platform_status"]["telegram"]["state"], "off")

    def test_explicit_disable_quarantines_bad_token_configuration(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "VK_ENABLED": "0",
            "VK_BOT_TOKEN": "broken-but-kept-for-later",
            "VK_GROUP_ID": "not-a-number",
        }
        with patch.dict(os.environ, env, clear=True):
            state = validate_environment(strict_platforms={"vk"})
        self.assertTrue(state["telegram"])
        self.assertFalse(state["vk"])
        self.assertEqual(state["platform_status"]["vk"]["state"], "off")


if __name__ == "__main__":
    unittest.main()
