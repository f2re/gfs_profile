from __future__ import annotations

import unittest

from telegram_ui import lead_keyboard, lead_page_count, lead_page_text


class TelegramUiTests(unittest.TestCase):
    def test_common_lead_keyboard_has_full_range_entry(self) -> None:
        markup = lead_keyboard(0)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("+24 ч сутки", labels)
        self.assertIn("Макс. +384 ч", labels)
        self.assertIn("Все сроки до +384 ч →", labels)

    def test_lead_pagination_has_multiple_pages(self) -> None:
        self.assertGreater(lead_page_count(), 1)
        markup = lead_keyboard(1)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("1/", " ".join(labels))
        self.assertIn("Популярные сроки", labels)

    def test_lead_page_text_mentions_max_forecast(self) -> None:
        self.assertIn("+384", lead_page_text(0))
        self.assertIn("страница", lead_page_text(1))


if __name__ == "__main__":
    unittest.main()
