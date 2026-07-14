from __future__ import annotations

import unittest

from telegram_commands import BOT_COMMANDS


class TelegramCommandTests(unittest.TestCase):
    def test_expected_commands_are_registered(self) -> None:
        names = [command.command for command in BOT_COMMANDS]
        self.assertEqual(
            names,
            [
                "start",
                "help",
                "profile",
                "route",
                "aero",
                "windgram",
                "cloudgram",
                "map",
                "cycle",
                "status",
                "admin",
                "cancel",
            ],
        )
        self.assertNotIn("skewt", names)

    def test_command_descriptions_are_present(self) -> None:
        for command in BOT_COMMANDS:
            self.assertTrue(command.description)
            self.assertLessEqual(len(command.description), 256)


if __name__ == "__main__":
    unittest.main()
