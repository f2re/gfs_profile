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
        self.assertFalse(state["max"])
        self.assertFalse(state["vk"])

    def test_max_token_requires_https_and_secret(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "tg-token",
            "MAX_BOT_TOKEN": "max-token",
            "MAX_WEBHOOK_URL": "http://example.test/webhooks/max",
            "MAX_WEBHOOK_SECRET": "secret_123",
        }
        with patch.dict(os.environ, env, clear=True), self.assertRaises(ConfigError):
            validate_environment()

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
        self.assertTrue(state["max"])
        self.assertTrue(state["vk"])
        self.assertEqual(state["port"], 8090)

    def test_telegram_token_is_always_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ConfigError):
            validate_environment()


if __name__ == "__main__":
    unittest.main()
