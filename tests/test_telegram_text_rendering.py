from __future__ import annotations

import unittest
from pathlib import Path

import telegram_meteogram
import telegram_schedules


class TelegramTextRenderingTests(unittest.TestCase):
    def test_meteogram_prompt_uses_real_line_breaks(self) -> None:
        text = telegram_meteogram._output_prompt("gfs", 3)
        self.assertIn("\n", text)
        self.assertNotIn("\\n", text)

    def test_ui_modules_have_no_double_escaped_newline_literals(self) -> None:
        for module in (telegram_meteogram, telegram_schedules):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("\\\\n", source, module.__name__)


if __name__ == "__main__":
    unittest.main()
