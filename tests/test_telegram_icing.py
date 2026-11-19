from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationHandlerStop
from gfs_core import GfsRun
from icing_config import IcingRequest
from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, Location, UiButton, UiKeyboard
from messenger.icing_router import IcingMessengerRouter, icing_card_keyboard, icing_owner, icing_payload
from messenger.icing_labels import BUTTON_LABELS as LABELS
from messenger.router import RouterDependencies
from messenger.state import FlowState
from messenger.user_recipes import UserRecipeStore
import telegram_icing as tg


def update(text="/icing", payload=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=42), effective_chat=SimpleNamespace(id=7), update_id=100,
        effective_message=SimpleNamespace(text=text, message_id=9, from_user=SimpleNamespace(id=999)),
        callback_query=SimpleNamespace(id="callback", data=payload) if payload else None,
    )


def location_update(lat=55.75, lon=37.62):
    result = update(None)
    result.effective_message.location = SimpleNamespace(latitude=lat, longitude=lon)
    return result


def point_keyboard():
    return UiKeyboard.from_rows([
        [UiButton("Геолокация", "request_location")],
        [UiButton("Назад", "callback", encode_callback("icing", "c", "request")),
         UiButton("Убрать пункт", "callback", encode_callback("icing", "d", "request"))],
    ])


class TelegramIcingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.built = []
        def builder(request, **kwargs):
            self.built.append(request)
            return CommonProductResult("icing", "GFS модель", [], {"run_date": "20261117", "run_cycle": "06"})
        self.router = IcingMessengerRouter(RouterDependencies(geocode=lambda *_: []),
            recipes=UserRecipeStore(Path(self.tmp.name) / "users.db"), icing_builder=builder,
            icing_run_selector=lambda request: (request.run or GfsRun("20261117", "06"), "index"))
        self.bot = AsyncMock()
        self.bot.send_message.return_value = SimpleNamespace(message_id=20)
        self.context = SimpleNamespace(bot=self.bot, user_data={})

    async def asyncTearDown(self):
        await self.router.shutdown_icing()
        self.tmp.cleanup()

    async def test_direct_command_uses_common_builder_without_city(self):
        # Other application tests may have initialized a cached runtime router.
        with patch.object(tg, "_ROUTER", object()), patch.object(tg, "get_router", return_value=self.router), patch("telegram_bot._clear_pending"):
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(update("/icing +24 product=slwp"), self.context)
            await self.router.icing_wait_idle()
        self.assertEqual(len(self.built), 1)
        self.assertEqual((self.built[0].product, self.built[0].lead), ("slwp", 24))
        self.assertIsNotNone(self.router.sessions.get("telegram", "42", "7"))
        self.assertIsNone(self.router.sessions.get("telegram", "999", "7"))

    async def test_native_home_callback_opens_same_six_product_menu(self):
        with patch.object(tg, "_ROUTER", object()), patch.object(tg, "get_router", return_value=self.router), patch("telegram_bot._clear_pending"):
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(update(payload="home:icing"), self.context)
        self.bot.answer_callback_query.assert_awaited_once()
        kwargs = self.bot.edit_message_text.call_args.kwargs
        labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertIn(LABELS["water_level"], labels)
        self.assertIn(LABELS["thickness"], labels)
        self.assertIn(LABELS["comparison"], labels)
        self.assertIn(LABELS["help"], labels)
        self.assertIn("17.11.2026 06:00 UTC", kwargs["text"])
        self.assertEqual(self.built, [])

    async def test_status_cancel_are_captured_only_for_icing(self):
        with patch.object(tg, "_ROUTER", self.router), patch.object(tg, "get_router", return_value=self.router):
            await tg._command_guard(update("/status"), self.context)
            self.assertEqual(self.bot.send_message.await_count, 0)
            self.context.user_data[tg.SESSION_KEY] = True
            await self.router._icing_menu(tg._normalized(update()), tg.TelegramIcingGateway(self.bot), IcingRequest())
            with self.assertRaises(ApplicationHandlerStop):
                await tg._command_guard(update("/status"), self.context)
            with self.assertRaises(ApplicationHandlerStop):
                await tg._command_guard(update("/cancel"), self.context)
            self.assertNotIn(tg.SESSION_KEY, self.context.user_data)

    async def test_unrelated_navigation_clears_only_icing_session(self):
        self.context.user_data[tg.SESSION_KEY] = True
        self.context.user_data[tg.LOCATION_KEYBOARD_KEY] = True
        event = tg._normalized(update())
        await self.router._icing_menu(event, tg.TelegramIcingGateway(self.bot), IcingRequest())
        with patch.object(tg, "_ROUTER", self.router):
            await tg._navigation_guard(update(payload="home:map"), self.context)
        self.assertNotIn(tg.SESSION_KEY, self.context.user_data)
        self.assertNotIn(tg.LOCATION_KEYBOARD_KEY, self.context.user_data)
        self.assertIsNone(self.router.sessions.get("telegram", "42", "7"))
        self.assertTrue(any(isinstance(call.kwargs.get("reply_markup"), ReplyKeyboardRemove)
                            for call in self.bot.send_message.call_args_list))

    async def test_geolocation_can_leave_regional_menu_for_existing_profile_flow(self):
        self.context.user_data[tg.SESSION_KEY] = True
        event = tg._normalized(update())
        await self.router._icing_menu(event, tg.TelegramIcingGateway(self.bot), IcingRequest())
        with patch.object(tg, "_ROUTER", self.router), patch.object(self.router, "handle", new_callable=AsyncMock) as handle:
            await tg._command_guard(location_update(), self.context)
            await tg.icing_update(location_update(), self.context)
            handle.assert_not_awaited()
        self.assertNotIn(tg.SESSION_KEY, self.context.user_data)
        self.assertIsNone(self.router.sessions.get("telegram", "42", "7"))

    async def test_location_is_normalized_and_captured_only_during_point_selection(self):
        for step in ("await_point", "point_search", "choose_point"):
            with self.subTest(step=step):
                state = FlowState(product="icing", step=step, params={"request": IcingRequest()})
                self.router.sessions.set("telegram", "42", "7", state)
                self.context.user_data[tg.SESSION_KEY] = True
                with patch.object(tg, "_ROUTER", self.router), patch.object(self.router, "handle", new_callable=AsyncMock) as handle:
                    await tg._command_guard(location_update(), self.context)
                    self.assertIs(self.router.sessions.get("telegram", "42", "7"), state)
                    with self.assertRaises(ApplicationHandlerStop):
                        await tg.icing_update(location_update(), self.context)
                event = handle.await_args.args[0]
                self.assertEqual(event.event_type, "LOCATION")
                self.assertEqual(event.location, Location(55.75, 37.62))
                self.assertTrue(self.context.user_data[tg.SESSION_KEY])

    async def test_fresh_generic_location_does_not_initialize_icing_router(self):
        with patch.object(tg, "_ROUTER", None), patch.object(tg, "get_router") as get_router:
            await tg._command_guard(location_update(), self.context)
            await tg.icing_update(location_update(), self.context)
        get_router.assert_not_called()
        self.bot.send_message.assert_not_awaited()

    async def test_native_location_prompt_sends_instead_of_editing_and_cleans_up(self):
        gateway = tg.TelegramIcingGateway(self.bot, self.context.user_data)
        result = await gateway.edit_text("7", "9", "Укажите пункт", keyboard=point_keyboard())
        self.assertEqual(result.message_id, "20")
        self.bot.edit_message_text.assert_not_awaited()
        markup = self.bot.send_message.call_args.kwargs["reply_markup"]
        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        self.assertTrue(markup.one_time_keyboard)
        self.assertTrue(markup.keyboard[0][0].request_location)
        self.assertEqual([button.text for row in markup.keyboard for button in row],
                         ["Геолокация", LABELS["back_card"], LABELS["remove_point"], LABELS["cancel_point"]])
        self.assertTrue(self.context.user_data[tg.LOCATION_KEYBOARD_KEY])

        # A new gateway instance shares the keyboard flag, just as the next update does.
        gateway = tg.TelegramIcingGateway(self.bot, self.context.user_data)
        await gateway.send_text("7", "Готово", keyboard=icing_card_keyboard(IcingRequest()))
        self.assertIsInstance(self.bot.send_message.call_args_list[-2].kwargs["reply_markup"], ReplyKeyboardRemove)
        self.bot.delete_message.assert_awaited_once_with(chat_id="7", message_id=20)
        self.assertNotIn(tg.LOCATION_KEYBOARD_KEY, self.context.user_data)

    async def test_cancel_removes_native_location_keyboard(self):
        self.context.user_data.update({tg.SESSION_KEY: True, tg.LOCATION_KEYBOARD_KEY: True})
        self.router.sessions.set("telegram", "42", "7", FlowState(
            product="icing", step="await_point", params={"request": IcingRequest()}))
        with patch.object(tg, "_ROUTER", self.router), patch.object(tg, "get_router", return_value=self.router):
            with self.assertRaises(ApplicationHandlerStop):
                await tg._command_guard(update("/cancel"), self.context)
        self.assertNotIn(tg.LOCATION_KEYBOARD_KEY, self.context.user_data)
        self.assertNotIn(tg.SESSION_KEY, self.context.user_data)
        self.assertIsNone(self.router.sessions.get("telegram", "42", "7"))
        self.bot.delete_message.assert_awaited_once()

    async def test_point_button_and_native_location_keep_forecast_and_pass_marker_to_builder(self):
        request = IcingRequest(product="slw_max", lead=48, run=GfsRun("20261117", "06"))
        payload = icing_payload("p", request, owner=icing_owner(tg._normalized(update())))
        with patch.object(tg, "_ROUTER", self.router), patch.object(tg, "get_router", return_value=self.router):
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(update(payload=payload), self.context)
            self.assertEqual(self.router.sessions.get("telegram", "42", "7").step, "await_point")
            self.assertTrue(self.context.user_data[tg.LOCATION_KEYBOARD_KEY])
            await tg._command_guard(location_update(), self.context)
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(location_update(), self.context)
            selected = self.router.sessions.get("telegram", "42", "7").params["request"]
            self.assertEqual((selected.product, selected.lead, selected.run), (request.product, request.lead, request.run))
            self.assertEqual(selected.point, (55.75, 37.62))
            self.assertNotIn(tg.LOCATION_KEYBOARD_KEY, self.context.user_data)
            keyboard = self.bot.send_message.call_args.kwargs["reply_markup"]
            run = next(button.callback_data for row in keyboard.inline_keyboard for button in row if button.text == LABELS["build"])
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(update(payload=run), self.context)
            await self.router.icing_wait_idle()
        self.assertEqual(self.built, [selected])

    async def test_native_back_buttons_keep_previous_point_and_remove_location_keyboard(self):
        request = IcingRequest(run=GfsRun("20261117", "06"), point=(59.94, 30.31))
        payload = icing_payload("p", request, owner=icing_owner(tg._normalized(update())))
        with patch.object(tg, "_ROUTER", self.router), patch.object(tg, "get_router", return_value=self.router):
            for label in ("Назад к карте", "Отмена выбора пункта", LABELS["back_card"], LABELS["cancel_point"]):
                with self.subTest(label=label):
                    with self.assertRaises(ApplicationHandlerStop):
                        await tg.icing_update(update(payload=payload), self.context)
                    with self.assertRaises(ApplicationHandlerStop):
                        await tg.icing_update(update(label), self.context)
                    state = self.router.sessions.get("telegram", "42", "7")
                    self.assertEqual(state.step, "params")
                    self.assertEqual(state.params["request"], request)
                    self.assertNotIn(tg.LOCATION_KEYBOARD_KEY, self.context.user_data)

    async def test_native_remove_point_accepts_current_and_previous_button_labels(self):
        request = IcingRequest(product="slwp", lead=48, run=GfsRun("20261117", "06"), point=(59.94, 30.31))
        payload = icing_payload("p", request, owner=icing_owner(tg._normalized(update())))
        with patch.object(tg, "_ROUTER", self.router), patch.object(tg, "get_router", return_value=self.router):
            for label in ("Убрать пункт", LABELS["remove_point"]):
                with self.subTest(label=label):
                    with self.assertRaises(ApplicationHandlerStop):
                        await tg.icing_update(update(payload=payload), self.context)
                    markup = self.bot.send_message.call_args.kwargs["reply_markup"]
                    self.assertIn(LABELS["remove_point"], [b.text for row in markup.keyboard for b in row])
                    with self.assertRaises(ApplicationHandlerStop):
                        await tg.icing_update(update(label), self.context)
                    state = self.router.sessions.get("telegram", "42", "7")
                    selected = state.params["request"]
                    self.assertEqual((selected.product, selected.lead, selected.run),
                                     (request.product, request.lead, request.run))
                    self.assertIsNone(selected.point)
                    self.assertEqual(state.step, "params")
                    self.assertNotIn(tg.LOCATION_KEYBOARD_KEY, self.context.user_data)

    async def test_native_point_menu_opens_complete_home_and_removes_reply_keyboard(self):
        request = IcingRequest(run=GfsRun("20261117", "06"))
        payload = icing_payload("p", request, owner=icing_owner(tg._normalized(update())))
        menu_update = update("Меню")
        marker = SimpleNamespace(delete=AsyncMock())
        menu_update.effective_message.reply_text = AsyncMock(return_value=marker)
        with patch.object(tg, "_ROUTER", self.router), patch.object(tg, "get_router", return_value=self.router), \
                patch("telegram_personal_ux.get_active_location", return_value=None), \
                patch("telegram_personal_ux.get_quick_preferences", return_value=[]):
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(update(payload=payload), self.context)
            markup = self.bot.send_message.call_args.kwargs["reply_markup"]
            self.assertIn(LABELS["home"], [button.text for row in markup.keyboard for button in row])
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(menu_update, self.context)
        self.assertNotIn(tg.SESSION_KEY, self.context.user_data)
        self.assertNotIn(tg.LOCATION_KEYBOARD_KEY, self.context.user_data)
        self.assertIsNone(self.router.sessions.get("telegram", "42", "7"))
        replies = menu_update.effective_message.reply_text.call_args_list
        self.assertTrue(any(isinstance(call.kwargs.get("reply_markup"), ReplyKeyboardRemove) for call in replies))
        callbacks = [button.callback_data for row in replies[-1].kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertIn("home:route", callbacks)
        self.assertIn("home:schedule", callbacks)
        self.assertIn("home:icing", callbacks)
        marker.delete.assert_awaited_once()

    async def test_cancel_button_clears_telegram_wizard_marker(self):
        self.context.user_data[tg.SESSION_KEY] = True
        event = tg._normalized(update())
        await self.router._icing_menu(event, tg.TelegramIcingGateway(self.bot), IcingRequest())
        keyboard = self.bot.send_message.call_args.kwargs["reply_markup"]
        cancel = next(button.callback_data for row in keyboard.inline_keyboard for button in row if button.text == LABELS["cancel"])
        with patch.object(tg, "get_router", return_value=self.router):
            with self.assertRaises(ApplicationHandlerStop):
                await tg.icing_update(update(payload=cancel), self.context)
        self.assertNotIn(tg.SESSION_KEY, self.context.user_data)

    async def test_full_png_export_uses_document_transport(self):
        path = Path(self.tmp.name) / "map.png"
        path.write_bytes(b"png")
        self.bot.send_document.return_value = SimpleNamespace(message_id=30)
        gateway = tg.TelegramIcingGateway(self.bot)
        await gateway.send_file("7", path, filename="icing_map.png")
        self.assertEqual(self.bot.send_document.call_args.kwargs["filename"], "icing_map.png")
        self.bot.send_photo.assert_not_awaited()


class TelegramIcingRegistrationTests(unittest.TestCase):
    def test_live_application_registers_icing_and_existing_map(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TESTTOKEN",
                "ALL_PROXY": "", "HTTPS_PROXY": "", "HTTP_PROXY": "",
                "all_proxy": "", "https_proxy": "", "http_proxy": ""}):
            import telegram_bot
            application = telegram_bot.build_application()
        commands = {name for handlers in application.handlers.values() for handler in handlers
                    for name in getattr(handler, "commands", ())}
        self.assertIn("icing", commands)
        self.assertIn("map", commands)
        self.assertIn("cancel", commands)
        self.assertTrue(any(getattr(handler, "callback", None) is tg.icing_update
                            for handler in application.handlers[-30]))

    def test_home_and_map_parameter_keyboards_expose_icing(self):
        import telegram_concise_ux
        import telegram_personal_ux
        from telegram_product_wizard import params_keyboard, start_map_wizard_state
        with patch.object(telegram_personal_ux, "get_quick_preferences", return_value=[]):
            keyboards = [telegram_concise_ux.home_keyboard(), telegram_personal_ux.home_keyboard(42),
                         params_keyboard(start_map_wizard_state())]
        for keyboard in keyboards:
            self.assertIn("home:icing", [b.callback_data for row in keyboard.inline_keyboard for b in row])

    def test_command_registry_is_short_and_russian(self):
        from telegram_commands import _ALL_BOT_COMMANDS
        command = next(c for c in _ALL_BOT_COMMANDS if c.command == "icing")
        self.assertIn("Обледенение", command.description)
        self.assertLessEqual(len(command.description), 40)
        from aero_single_mode import _human_help_text
        self.assertIn("/icing", _human_help_text())

    def test_markup_preserves_common_payloads(self):
        common = icing_card_keyboard(IcingRequest())
        native = tg.telegram_markup(common)
        self.assertEqual([["prefs:home" if b.payload == "v1|flow|home" else b.payload for b in row] for row in common.rows],
                         [[b.callback_data for b in row] for row in native.inline_keyboard])


if __name__ == "__main__":
    unittest.main()
