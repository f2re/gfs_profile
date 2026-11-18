from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, Mock, patch

import register_messenger_commands as registration
from messenger.commands import public_command_specs
from messenger.max import MaxApiClient


MAX_ENV = {
    "MAX_BOT_TOKEN": "max-secret", "MAX_WEBHOOK_URL": "https://example.test/webhooks/max",
    "MAX_WEBHOOK_SECRET": "example_secret", "WEATHERNEXT3_ENABLED": "0",
}


def expected_max_commands():
    return [{"name": name, "description": description} for name, description in public_command_specs("max")]


class MessengerCommandRegistrationTests(unittest.TestCase):
    def test_max_wire_contract_and_readback(self):
        session = Mock()
        update, info = Mock(), Mock()
        for response in (update, info):
            response.status_code = 200
        update.json.return_value = {"commands": expected_max_commands()}
        info.json.return_value = {"user_id": 1, "is_bot": True, "commands": expected_max_commands()}
        session.request.side_effect = [update, info]
        client = MaxApiClient("max-secret", session=session, retries=0)
        registration.register_max_commands(client=client)
        first, second = session.request.call_args_list
        self.assertEqual(first.args, ("PATCH", "https://platform-api2.max.ru/me/commands"))
        self.assertEqual(first.kwargs["json"], {"commands": expected_max_commands()})
        self.assertEqual(first.kwargs["headers"], {"Authorization": "max-secret"})
        self.assertIsNone(first.kwargs["params"])
        self.assertEqual(second.args, ("GET", "https://platform-api2.max.ru/me"))
        self.assertIn("icing", [c["name"] for c in first.kwargs["json"]["commands"]])
        self.assertNotIn("wn3", [c["name"] for c in first.kwargs["json"]["commands"]])

    def test_max_readback_retries_bounded_and_detects_stale_menu(self):
        client = Mock()
        client.get_bot_info.side_effect = [{"commands": []}, {"commands": expected_max_commands()}]
        with patch.object(registration.time, "sleep") as sleep:
            registration.register_max_commands(client=client)
        self.assertEqual(client.get_bot_info.call_count, 2)
        sleep.assert_called_once_with(0.5)
        client.get_bot_info.side_effect = None
        client.get_bot_info.return_value = {"commands": []}
        client.get_bot_info.reset_mock()
        with patch.object(registration.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "проверка команд"):
                registration.register_max_commands(client=client)
        self.assertEqual(client.get_bot_info.call_count, 3)

    def test_max_status_does_not_change_commands(self):
        client = Mock()
        client.get_bot_info.return_value = {"commands": expected_max_commands()}
        registration.register_max_commands(status_only=True, client=client)
        client.set_commands.assert_not_called()

    def _run(self, env, *, tg_error=None, max_error=None, args=()):
        output = io.StringIO()
        tg = AsyncMock(side_effect=tg_error)
        mx = Mock(side_effect=max_error)
        with patch.dict(os.environ, env, clear=True), \
             patch.object(registration, "_load_env_file"), \
             patch.object(registration, "_register", tg), \
             patch.object(registration, "register_max_commands", mx), \
             patch("sys.argv", ["register_messenger_commands.py", *args]), \
             redirect_stdout(output):
            result = registration.main()
        return result, output.getvalue(), tg, mx

    def test_disabled_telegram_and_max_only_do_not_require_telegram_token(self):
        result, _, tg, mx = self._run({**MAX_ENV, "TELEGRAM_ENABLED": "0"})
        self.assertEqual(result, 0)
        tg.assert_not_awaited()
        mx.assert_called_once_with(status_only=False)

    def test_all_disabled_web_only_skips_network(self):
        result, _, tg, mx = self._run({})
        self.assertEqual(result, 0)
        tg.assert_not_awaited()
        mx.assert_not_called()

    def test_telegram_only_launcher_does_not_update_inactive_max_menu(self):
        result, _, tg, mx = self._run(
            {**MAX_ENV, "TELEGRAM_BOT_TOKEN": "tg-secret", "MESSENGER_RUNTIME_ENABLED": "0"},
            args=("--telegram",),
        )
        self.assertEqual(result, 0)
        tg.assert_awaited_once_with(status_only=False)
        mx.assert_not_called()

    def test_explicit_max_does_not_require_other_platform_configuration(self):
        result, _, tg, mx = self._run({**MAX_ENV, "TELEGRAM_ENABLED": "1"}, args=("--max",))
        self.assertEqual(result, 0)
        tg.assert_not_awaited()
        mx.assert_called_once_with(status_only=False)

    def test_requested_missing_token_is_failure(self):
        result, output, tg, mx = self._run({"TELEGRAM_ENABLED": "1"})
        self.assertEqual(result, 3)
        self.assertIn("TELEGRAM_BOT_TOKEN не задан", output)
        tg.assert_not_awaited()
        mx.assert_not_called()

    def test_one_failure_does_not_skip_other_enabled_platform_or_expose_token(self):
        result, output, tg, mx = self._run(
            {**MAX_ENV, "TELEGRAM_BOT_TOKEN": "tg-secret"},
            tg_error=RuntimeError("https://api.telegram.org/bottg-secret/getMe"),
        )
        self.assertEqual(result, 3)
        tg.assert_awaited_once()
        mx.assert_called_once()
        self.assertNotIn("tg-secret", output)
        self.assertNotIn("max-secret", output)

    def test_status_cli_is_read_only_for_both_platforms(self):
        result, _, tg, mx = self._run({**MAX_ENV, "TELEGRAM_BOT_TOKEN": "tg-secret"}, args=("--status",))
        self.assertEqual(result, 0)
        tg.assert_awaited_once_with(status_only=True)
        mx.assert_called_once_with(status_only=True)

    def test_shared_catalog_keeps_supported_product_commands_equal(self):
        with patch.dict(os.environ, {"WEATHERNEXT3_ENABLED": "0"}):
            tg = dict(public_command_specs("telegram"))
            mx = dict(public_command_specs("max"))
        self.assertEqual({name: description for name, description in tg.items() if name != "cycle"}, mx)
        self.assertIn("icing", mx)
        self.assertNotIn("wn3", mx)
        self.assertLessEqual(len(mx), 32)
        self.assertTrue(all(1 <= len(name) <= 64 and 1 <= len(text) <= 128 for name, text in mx.items()))


if __name__ == "__main__":
    unittest.main()
