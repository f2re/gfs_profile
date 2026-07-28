from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import register_telegram_commands as registration
from telegram_commands import BOT_COMMANDS


class RegisterTelegramCommandsTests(unittest.TestCase):
    def test_all_private_and_language_scopes_are_synchronized(self) -> None:
        bot = AsyncMock()
        with patch.object(registration, "Bot", return_value=bot), patch.object(
            registration, "_token_from_env", return_value="token"
        ):
            asyncio.run(registration._register())

        self.assertEqual(bot.delete_my_commands.await_count, 6)
        self.assertEqual(bot.set_my_commands.await_count, 4)
        for call in bot.set_my_commands.await_args_list:
            self.assertEqual(call.args[0], list(BOT_COMMANDS))


if __name__ == "__main__":
    unittest.main()
