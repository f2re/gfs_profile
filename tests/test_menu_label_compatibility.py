"""Old reply-keyboard text must remain usable after menu styling changes."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram import Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop, MessageHandler

from messenger.icing_labels import point_reply_action
from messenger.menu_catalog import CANCEL_TEXT, is_cancel_text
import telegram_user_state


class MenuLabelCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = patch.object(telegram_user_state, "DEFAULT_DB_PATH", Path(self.tmp.name) / "users.sqlite3")
        self.db.start()
        self.addCleanup(self.db.stop)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TESTTOKEN", "WEATHERNEXT3_ENABLED": "0"}):
            import telegram_bot
            self.module = telegram_bot
            self.application = telegram_bot.build_application()

    async def test_old_and_current_cancel_texts_exit_real_point_handlers(self):
        import telegram_icing
        import telegram_meteogram

        user = User(42, "Tester", is_bot=False)
        for key, state in (
            ("product_wizard", {"product": "cloudgram", "step": "await_point"}),
            ("meteogram_wizard", {"step": "point"}),
        ):
            for label in ("✖ Отмена", "✕ Отмена", CANCEL_TEXT):
                with self.subTest(flow=key, label=label):
                    message = Message(1, datetime.now(timezone.utc), Chat(42, "private"),
                                      from_user=user, text=label)
                    update = Update(1, message=message)
                    context = SimpleNamespace(user_data={key: dict(state)}, args=[],
                                              bot=AsyncMock(), application=self.application)
                    reply = AsyncMock(return_value=SimpleNamespace(delete=AsyncMock()))
                    with (
                        patch.object(Message, "reply_text", reply),
                        patch.object(self.module, "record_telegram_user"),
                        patch.object(self.module, "search_location_candidates") as profile_search,
                        patch.object(telegram_meteogram, "search_location_candidates") as meteo_search,
                        patch.object(telegram_icing, "_ROUTER", None),
                    ):
                        try:
                            for group in sorted(self.application.handlers):
                                for handler in self.application.handlers[group]:
                                    if isinstance(handler, MessageHandler) and handler.check_update(update):
                                        await handler.callback(update, context)
                                        break
                        except ApplicationHandlerStop:
                            pass
                    self.assertNotIn(key, context.user_data)
                    profile_search.assert_not_called()
                    meteo_search.assert_not_called()
                    self.assertTrue(any("GFS data" in str(call.args[0]) for call in reply.await_args_list))

    def test_icing_reply_navigation_accepts_previously_sent_labels(self):
        for action, labels in {
            "back": ("← Назад", "← К карте", "↩ Отмена выбора пункта", "⬅️ Назад", "⬅️ К карте"),
            "cancel": ("Отмена", "✖ Отмена", "✕ Отмена", CANCEL_TEXT),
            "home": ("Меню", "🏠 Меню"),
        }.items():
            for label in labels:
                with self.subTest(action=action, label=label):
                    self.assertEqual(point_reply_action(label), action)
        for label in ("✖ Отмена", "✕ Отмена", CANCEL_TEXT, "Отмена", "/cancel"):
            self.assertTrue(is_cancel_text(label))
        for label in (None, "", "Отмена поездки", "Меню города"):
            self.assertFalse(is_cancel_text(label))
            self.assertIsNone(point_reply_action(label))


if __name__ == "__main__":
    unittest.main()
