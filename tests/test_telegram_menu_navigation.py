from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from messenger.menu_catalog import PRODUCT_LABELS, product_rows
import telegram_personal_ux as personal
import telegram_product_wizard as wizard
import telegram_user_state
from telegram_ui import canonical_leads, lead_keyboard, lead_page_count


class TelegramMenuNavigationTests(unittest.IsolatedAsyncioTestCase):
    """Walk callbacks after every production compatibility layer is installed."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(telegram_user_state, "DEFAULT_DB_PATH", Path(self.tmp.name) / "users.sqlite3")
        self.db_patch.start()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TESTTOKEN"}):
            import telegram_bot
            self.module = telegram_bot
            self.application = telegram_bot.build_application()
        self.record_patch = patch.object(telegram_bot, "record_telegram_user")
        self.record_patch.start()
        self.user = SimpleNamespace(id=42, username="tester", first_name="Tester", last_name=None,
                                    full_name="Tester", is_bot=False, language_code="ru")
        self.message = SimpleNamespace(
            message_id=77, text="Меню", from_user=self.user,
            reply_text=AsyncMock(return_value=SimpleNamespace(delete=AsyncMock())),
        )
        self.context = SimpleNamespace(user_data={}, args=[], bot=AsyncMock(), application=self.application)

    async def asyncTearDown(self):
        self.record_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    async def press(self, payload):
        query = SimpleNamespace(id="callback", data=payload, message=self.message,
                                answer=AsyncMock(), edit_message_text=AsyncMock(),
                                edit_message_reply_markup=AsyncMock())
        update = SimpleNamespace(update_id=1, callback_query=query, effective_user=self.user,
                                 effective_message=self.message,
                                 effective_chat=SimpleNamespace(id=42, type="private"))
        try:
            for group in sorted(self.application.handlers):
                for handler in self.application.handlers[group]:
                    if not isinstance(handler, CallbackQueryHandler):
                        continue
                    pattern = handler.pattern
                    if pattern is not None and not pattern.match(payload):
                        continue
                    await handler.callback(update, self.context)
                    break
        except ApplicationHandlerStop:
            pass
        return query

    async def test_menu_button_leaves_all_old_wizards(self):
        stale = {key: {"step": "await_point"} for key in (
            "product_wizard", "pending_profile", "meteogram_wizard", "route_profile_wizard",
            "personal_profile", "personal_meteogram", "schedule_product_setup",
        )}
        self.context.user_data.update(stale)
        query = await self.press("prefs:home")
        self.assertFalse(stale.keys() & self.context.user_data.keys())
        self.assertIn("GFS data", query.edit_message_text.call_args.args[0])
        callbacks = {button.callback_data for row in query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard for button in row}
        self.assertIn("home:icing", callbacks)
        self.assertIn("home:route", callbacks)

    async def test_stale_disabled_provider_exits_on_first_menu_click(self):
        self.context.user_data["weathernext3_wizard"] = {"product": "weathernext3"}
        self.message.text = "/wn3 Москва"
        with patch.dict(os.environ, {"WEATHERNEXT3_ENABLED": "0"}):
            query = await self.press("prefs:home")
        query.edit_message_text.assert_awaited_once()
        self.assertIn("GFS data", query.edit_message_text.call_args.args[0])
        self.assertNotIn("weathernext3_wizard", self.context.user_data)

    async def test_opening_product_replaces_route_and_schedule_selection(self):
        self.context.user_data.update({"route_profile_wizard": {"step": "await_route"},
                                       "schedule_product_setup": {"product": "meteogram"}})
        await self.press("home:cloudgram")
        self.assertNotIn("route_profile_wizard", self.context.user_data)
        self.assertNotIn("schedule_product_setup", self.context.user_data)
        self.assertEqual(self.context.user_data["product_wizard"]["product"], "cloudgram")
        self.assertEqual(self.context.user_data["product_wizard"]["step"], "await_point")
        await self.press("wiz:cancel")
        self.assertNotIn("product_wizard", self.context.user_data)

    async def test_direct_product_command_exits_previous_input_flow(self):
        self.context.user_data["meteogram_wizard"] = {"step": "point"}
        self.message.text = "/route"
        update = SimpleNamespace(effective_message=self.message, effective_user=self.user)
        handler = next(handler for handlers in self.application.handlers.values() for handler in handlers
                       if getattr(handler.callback, "__name__", "") == "reset_command_navigation")
        await handler.callback(update, self.context)
        import telegram_route
        with patch.object(telegram_route, "record_telegram_user"):
            await telegram_route.route_command(update, self.context)
        self.assertNotIn("meteogram_wizard", self.context.user_data)
        self.assertEqual(self.context.user_data["route_profile_wizard"]["step"], "await_route")
        await self.press("route:cancel")
        self.assertNotIn("route_profile_wizard", self.context.user_data)

    async def test_saved_profile_all_leads_preserves_point(self):
        point = {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "saved"}
        self.context.user_data["personal_profile"] = {"point": point, "lead": 24}
        await self.press("personal:profile:lead:3")
        self.assertEqual(self.context.user_data["personal_profile"]["lead"], 3)
        query = await self.press("personal:profile:all")
        self.assertEqual(self.context.user_data["pending_profile"]["point"]["lat"], 55.75)
        self.assertIn("Москва", query.edit_message_text.call_args.args[0])
        leads = {int(button.callback_data.split(":")[1])
                 for page in range(lead_page_count() + 1)
                 for row in lead_keyboard(page).inline_keyboard for button in row
                 if str(button.callback_data).startswith("lead:")}
        self.assertEqual(leads, set(canonical_leads()))
        await self.press("cancel")
        self.assertNotIn("pending_profile", self.context.user_data)
        self.assertNotIn("personal_profile", self.context.user_data)

    async def test_personal_home_uses_shared_product_order_and_wide_labels(self):
        keyboard = personal.home_keyboard(42)
        product_buttons = [[button for button in row if str(button.callback_data).startswith("home:")
                            and str(button.callback_data).split(":")[1] in PRODUCT_LABELS]
                           for row in keyboard.inline_keyboard]
        rows = [tuple(button.callback_data.split(":")[1] for button in row) for row in product_buttons if row]
        self.assertEqual(tuple(rows), product_rows(set(PRODUCT_LABELS) - {"weathernext3"}))
        for row in product_buttons:
            self.assertLessEqual(len(row), 2)
            for button in row:
                self.assertEqual(button.text, PRODUCT_LABELS[button.callback_data.split(":")[1]])

    async def test_dense_wizard_options_keep_callbacks_and_fit_three_columns(self):
        states = [wizard.start_aero_wizard_state(24), wizard.start_windgram_wizard_state(),
                  wizard.start_cloudgram_wizard_state(), wizard.start_map_wizard_state(24),
                  {**wizard.start_map_wizard_state(24), "mode": "gif"}]
        for state in states:
            keyboard = wizard.params_keyboard(state)
            for row in keyboard.inline_keyboard:
                numeric = all(str(button.callback_data).rsplit(":", 1)[-1].isdigit() for button in row)
                self.assertLessEqual(len(row), 3 if numeric else 2, (state["product"], row))
                self.assertTrue(all(button.callback_data for button in row))


if __name__ == "__main__":
    unittest.main()
