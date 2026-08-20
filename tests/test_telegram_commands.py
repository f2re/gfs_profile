from __future__ import annotations

import unittest

from aero_single_mode import _human_help_text, _human_home_text
from telegram_commands import BOT_COMMANDS, BOT_COMMAND_LINES


class TelegramCommandTests(unittest.TestCase):
    def test_expected_public_commands_are_registered(self) -> None:
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
                "meteogram",
                "map",
                "schedule",
                "settings",
                "cycle",
                "status",
                "cancel",
            ],
        )
        self.assertNotIn("skewt", names)
        self.assertNotIn("admin", names)

    def test_admin_is_not_exposed_in_public_copy(self) -> None:
        public_copy = "\n".join((_human_home_text(), _human_help_text(), *BOT_COMMAND_LINES))
        self.assertNotIn("/admin", public_copy)
        self.assertNotIn("Администрирование", public_copy)

    def test_command_descriptions_are_present(self) -> None:
        for command in BOT_COMMANDS:
            self.assertTrue(command.description)
            self.assertLessEqual(len(command.description), 256)


if __name__ == "__main__":
    unittest.main()
