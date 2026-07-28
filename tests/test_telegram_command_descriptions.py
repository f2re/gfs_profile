from __future__ import annotations

import unittest

from telegram_commands import BOT_COMMANDS


class TelegramCommandDescriptionTests(unittest.TestCase):
    def test_start_opens_main_menu_not_location(self) -> None:
        descriptions = {command.command: command.description for command in BOT_COMMANDS}
        self.assertEqual(descriptions["start"], "🌦 Главное меню")
        self.assertNotIn("геолокац", descriptions["start"].lower())

    def test_descriptions_are_short(self) -> None:
        for command in BOT_COMMANDS:
            self.assertLessEqual(len(command.description), 40)


if __name__ == "__main__":
    unittest.main()
