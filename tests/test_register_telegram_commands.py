from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import register_telegram_commands as registration
from telegram import BotCommand
from telegram_commands import BOT_COMMANDS, synchronize_bot_commands


class StatefulBot:
    def __init__(self):
        self.menus = {(scope, language): [BotCommand("old", "Старое меню")]
                      for scope in ("default", "all_private_chats")
                      for language in ("", "ru", "en")}
        self.events = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def set_my_commands(self, commands, *, scope, language_code=""):
        self.events.append(("set", scope.type, language_code))
        self.menus[(scope.type, language_code)] = list(commands)
        return True

    async def delete_my_commands(self, *, scope, language_code=""):
        self.events.append(("delete", scope.type, language_code))
        self.menus.pop((scope.type, language_code), None)
        return True

    async def get_my_commands(self, *, scope, language_code=""):
        self.events.append(("get", scope.type, language_code))
        return self.menus.get((scope.type, language_code), [])


class RegisterTelegramCommandsTests(unittest.TestCase):
    def test_all_private_and_language_scopes_are_synchronized(self) -> None:
        bot = StatefulBot()
        with patch.object(registration, "Bot", return_value=bot), patch.object(
            registration, "_token_from_env", return_value="token"
        ):
            asyncio.run(registration._register())

        self.assertEqual(sum(event[0] == "delete" for event in bot.events), 2)
        self.assertEqual(sum(event[0] == "set" for event in bot.events), 4)
        self.assertEqual(sum(event[0] == "get" for event in bot.events), 6)
        for scope in ("default", "all_private_chats"):
            for language in ("", "ru"):
                self.assertEqual(bot.menus[(scope, language)], list(BOT_COMMANDS))
            self.assertNotIn((scope, "en"), bot.menus)
        self.assertTrue(bot.closed)
        self.assertEqual(bot.events[:3], [
            ("set", "default", ""), ("set", "default", "ru"),
            ("delete", "default", "en"),
        ])

    def test_failed_replace_keeps_old_menu_and_does_not_report_success(self):
        bot = StatefulBot()
        bot.set_my_commands = AsyncMock(side_effect=RuntimeError("network failed"))
        with self.assertRaises(RuntimeError):
            asyncio.run(synchronize_bot_commands(bot, verify=True))
        self.assertEqual(len(bot.menus), 6)
        self.assertFalse(any(event[0] == "delete" for event in bot.events))

    def test_readback_mismatch_fails_after_bounded_retry(self):
        bot = StatefulBot()
        bot.get_my_commands = AsyncMock(return_value=[BotCommand("old", "Старое меню")])
        with patch("telegram_commands.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaisesRegex(RuntimeError, "проверка команд"):
                asyncio.run(synchronize_bot_commands(bot, verify=True))
        self.assertEqual(bot.get_my_commands.await_count, 18)
        self.assertEqual(sleep.await_count, 2)

    def test_feature_flag_is_read_at_registration_not_import(self):
        bot = StatefulBot()
        with patch.dict(os.environ, {"WEATHERNEXT3_ENABLED": "1"}):
            asyncio.run(synchronize_bot_commands(bot, verify=True))
        self.assertIn("wn3", [c.command for c in bot.menus[("default", "")]])
        with patch.dict(os.environ, {"WEATHERNEXT3_ENABLED": "0"}):
            asyncio.run(synchronize_bot_commands(bot, verify=True))
        commands = [c.command for c in bot.menus[("default", "")]]
        self.assertNotIn("wn3", commands)
        self.assertIn("icing", commands)

    def test_status_reads_without_mutating_commands(self):
        bot = StatefulBot()
        asyncio.run(synchronize_bot_commands(bot))
        bot.events.clear()
        with patch.object(registration, "Bot", return_value=bot), patch.object(
            registration, "_token_from_env", return_value="token"
        ):
            asyncio.run(registration._register(status_only=True))
        self.assertTrue(bot.events)
        self.assertTrue(all(event[0] == "get" for event in bot.events))


if __name__ == "__main__":
    unittest.main()
