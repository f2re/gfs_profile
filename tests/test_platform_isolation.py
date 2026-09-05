from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI

import messenger_runtime
import register_messenger_webhooks as registration
from messenger.platform_config import PlatformStatus
from messenger.webhooks import MessengerWebhookService


class _Router:
    async def handle(self, event, gateway):
        return None


class PlatformIsolationTests(unittest.TestCase):
    def test_broken_vk_does_not_disable_ready_max_gateway(self) -> None:
        env = {
            "TELEGRAM_ENABLED": "0",
            "MAX_BOT_TOKEN": "max-token",
            "MAX_WEBHOOK_URL": "https://bot.example.test/webhooks/max",
            "MAX_WEBHOOK_SECRET": "secret_123",
            "VK_BOT_TOKEN": "vk-token",
            "VK_GROUP_ID": "broken",
            "VK_CALLBACK_URL": "https://bot.example.test/webhooks/vk",
        }
        with patch.dict(os.environ, env, clear=True):
            service = MessengerWebhookService.from_env(_Router())
        self.assertIsNotNone(service.max_gateway)
        self.assertIsNone(service.vk_gateway)
        self.assertEqual(service.platform_status["max"].state, "ready")
        self.assertEqual(service.platform_status["vk"].state, "degraded")

    def test_explicit_vk_disable_ignores_stale_broken_settings(self) -> None:
        env = {
            "VK_ENABLED": "0",
            "VK_BOT_TOKEN": "old-token",
            "VK_GROUP_ID": "broken",
        }
        with patch.dict(os.environ, env, clear=True):
            service = MessengerWebhookService.from_env(_Router())
        self.assertIsNone(service.vk_gateway)
        self.assertEqual(service.platform_status["vk"].state, "off")

    def test_normal_registration_is_best_effort(self) -> None:
        good = registration.RegistrationResult("max", "https://m", "ok", True)
        bad = registration.RegistrationResult("vk", "https://v", "broken", False)
        with (
            patch("register_messenger_webhooks.max_status", return_value=PlatformStatus.active("max")),
            patch("register_messenger_webhooks.vk_status", return_value=PlatformStatus.active("vk")),
            patch("register_messenger_webhooks.register_max", return_value=good),
            patch("register_messenger_webhooks.register_vk", return_value=bad),
            patch("sys.argv", ["register_messenger_webhooks.py"]),
        ):
            self.assertEqual(registration.main(), 0)

    def test_status_check_is_strict(self) -> None:
        bad = registration.RegistrationResult("vk", "https://v", "broken", False)
        with (
            patch("register_messenger_webhooks.max_status", return_value=PlatformStatus.off("max")),
            patch("register_messenger_webhooks.vk_status", return_value=PlatformStatus.active("vk")),
            patch("register_messenger_webhooks.status_vk", return_value=bad),
            patch("sys.argv", ["register_messenger_webhooks.py", "--status"]),
        ):
            self.assertEqual(registration.main(), 3)


class TelegramLifecycleIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_start_failure_does_not_raise(self) -> None:
        fake_app = FastAPI()
        fake_app.state.platform_runtime = {
            "telegram": PlatformStatus.active("telegram").as_dict(),
            "max": PlatformStatus.active("max").as_dict(),
            "vk": PlatformStatus.off("vk").as_dict(),
        }
        application = SimpleNamespace(
            initialize=AsyncMock(side_effect=RuntimeError("telegram unavailable")),
            updater=None,
            running=False,
            shutdown=AsyncMock(),
        )
        with patch("telegram_bot.build_application", return_value=application):
            result = await messenger_runtime._start_telegram(fake_app, PlatformStatus.active("telegram"))
        self.assertIsNone(result)
        self.assertEqual(fake_app.state.platform_runtime["telegram"]["state"], "degraded")
        self.assertEqual(fake_app.state.platform_runtime["max"]["state"], "ready")

    async def test_health_reports_degraded_provider_without_failing_runtime(self) -> None:
        old_runtime = getattr(messenger_runtime.app.state, "platform_runtime", None)
        old_ready = getattr(messenger_runtime.app.state, "runtime_ready", None)
        try:
            messenger_runtime.app.state.runtime_ready = True
            messenger_runtime.app.state.platform_runtime = {
                "telegram": PlatformStatus.active("telegram").as_dict(),
                "max": PlatformStatus.active("max").as_dict(),
                "vk": PlatformStatus.degraded("vk", "bad token").as_dict(),
            }
            result = await messenger_runtime.health()
            self.assertEqual(result["status"], "degraded")
            self.assertTrue(result["platforms"]["telegram"])
            self.assertTrue(result["platforms"]["max"])
            self.assertFalse(result["platforms"]["vk"])
        finally:
            if old_runtime is None:
                messenger_runtime.app.state.__dict__.pop("platform_runtime", None)
            else:
                messenger_runtime.app.state.platform_runtime = old_runtime
            if old_ready is None:
                messenger_runtime.app.state.__dict__.pop("runtime_ready", None)
            else:
                messenger_runtime.app.state.runtime_ready = old_ready


if __name__ == "__main__":
    unittest.main()
