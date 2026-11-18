from __future__ import annotations

import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from geocode import GeoPoint
from gfs_core import GfsRun
from messenger.callback_codec import encode_callback
from messenger.contracts import CommonProductResult, NormalizedEvent, PlatformMessage
from messenger.router import MessengerRouter, RouterDependencies


class Gateway:
    def __init__(self, platform):
        self.platform, self.calls = platform, []

    async def send_text(self, chat_id, text, *, keyboard=None, **kwargs):
        self.calls.append(("send", text, keyboard))
        return PlatformMessage(self.platform, chat_id, str(len(self.calls)))

    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, **kwargs):
        self.calls.append(("edit", text, keyboard))
        return PlatformMessage(self.platform, chat_id, message_id)

    async def answer_callback(self, event, **kwargs):
        self.calls.append(("answer", "", None))


class ForecastMessengerFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_menu_check_never_restores_cancelled_flow(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def lookup(*args, **kwargs):
            started.set()
            await release.wait()
            return GfsRun("20261118", "00")
        router = MessengerRouter(RouterDependencies(geocode=lambda *_: [GeoPoint(55.75, 37.62, "Москва", "test")]))
        gateway = Gateway("max")
        with patch("messenger.router.ensure_menu_run", side_effect=lookup):
            task = asyncio.create_task(router.handle(NormalizedEvent("max", "1", "TEXT", "user", "chat", text="Москва"), gateway))
            await started.wait()
            await router.handle(NormalizedEvent("max", "2", "COMMAND", "user", "chat", text="/cancel", command="cancel"), gateway)
            release.set()
            await task
        self.assertIsNone(router.sessions.get("max", "user", "chat"))
        self.assertIn("Выбор сброшен", gateway.calls[-1][1])
        self.assertFalse(any("Данные GFS:" in call[1] for call in gateway.calls))

    async def test_profile_menu_and_fallback_have_same_semantics_in_each_platform(self):
        recent, previous = GfsRun("20261118", "06"), GfsRun("20261118", "00")
        for platform in ("telegram", "max", "vk"):
            with self.subTest(platform=platform):
                selected = []
                selector = Mock(side_effect=lambda lead: recent if lead == 24 else previous)
                def builder(point, lead, run, **kwargs):
                    selected.append((lead, run))
                    return CommonProductResult("profile", "Готово", [], {})
                router = MessengerRouter(RouterDependencies(
                    geocode=lambda *_: [GeoPoint(55.75, 37.62, "Москва", "test")],
                    profile_builder=builder,
                    latest_run_for_lead=selector,
                ), progress_interval_seconds=0.25)
                gateway = Gateway(platform)
                await router.handle(NormalizedEvent(platform, "1", "TEXT", "user", "chat", text="Москва"), gateway)
                menu = gateway.calls[-1]
                self.assertIn("Данные GFS: 18.11.2026 06:00 UTC", menu[1])
                self.assertIn("Прогноз +24 ч → 19.11.2026 06:00 UTC", menu[1])
                buttons = [button for row in menu[2].rows for button in row]
                self.assertTrue(any(button.text == "+24 · 19.11 06:00" for button in buttons))
                page = NormalizedEvent(platform, "2", "CALLBACK", "user", "chat",
                                       callback_payload=encode_callback("profile", "leadpage", 1), callback_id="2", message_id="menu")
                before = len(gateway.calls)
                await router.handle(page, gateway)
                self.assertEqual(selector.call_count, 1)
                self.assertFalse(any(call[0] == "send" for call in gateway.calls[before:]))
                select = NormalizedEvent(platform, "3", "CALLBACK", "user", "chat",
                                         callback_payload=encode_callback("profile", "lead", 384), callback_id="3")
                await router.handle(select, gateway)
                self.assertEqual(selected, [(384, previous)])
                self.assertEqual([call.args[0] for call in selector.call_args_list], [24, 384])
                self.assertTrue(any("Для выбранного срока доступен другой цикл" in call[1] and
                                    "04.12.2026 00:00 UTC" in call[1] for call in gateway.calls))


class Message:
    def __init__(self):
        self.calls = []
        self.from_user = SimpleNamespace(id=0)
        self.location = SimpleNamespace(latitude=55.75, longitude=37.62)

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self

    async def delete(self):
        pass


class ForecastTelegramFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_telegram_menu_is_not_restored_after_lookup(self):
        import telegram_bot as bot
        started, release = asyncio.Event(), asyncio.Event()
        async def lookup(*args, **kwargs):
            started.set()
            await release.wait()
            return GfsRun("20261118", "00")
        message = Message()
        point = GeoPoint(55.75, 37.62, "Москва", "test")
        context = SimpleNamespace(user_data={})
        bot._set_pending_point(context, point)
        with patch.object(bot, "ensure_menu_run", side_effect=lookup):
            task = asyncio.create_task(bot._show_profile_leads(message, context, point))
            await started.wait()
            bot._clear_pending(context)
            release.set()
            await task
        self.assertNotIn("pending_profile", context.user_data)
        self.assertFalse(any("Данные GFS:" in call[0] for call in message.calls))

    async def test_composed_telegram_geo_picker_pagination_and_long_lead(self):
        import telegram_bot as bot
        message = Message()
        context = SimpleNamespace(user_data={})
        update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=0), callback_query=None)
        recent, previous = GfsRun("20261118", "06"), GfsRun("20261118", "00")
        selector = Mock(side_effect=lambda lead: recent if lead == 24 else previous)
        with patch.object(bot, "latest_available_run_for_lead", selector), \
             patch.object(bot, "remember_location"), \
             patch.object(bot, "_tracked_run_profile", new_callable=AsyncMock) as runner:
            await bot.location_message(update, context)
            text, kwargs = message.calls[-1]
            self.assertIn("19.11.2026 06:00 UTC", text)
            self.assertTrue(any(button.text == "+24 · 19.11 06:00" for row in kwargs["reply_markup"].inline_keyboard for button in row))
            query = SimpleNamespace(data="leadpage:1", message=message, answer=AsyncMock(), edit_message_text=message.edit_text)
            update.callback_query = query
            await bot.lead_page_callback(update, context)
            self.assertEqual(selector.call_count, 1)
            query.data = "lead:384"
            await bot.lead_callback(update, context)
            self.assertEqual(runner.await_args.args[2:4], (384, previous))
            self.assertIn("04.12.2026 00:00 UTC", message.calls[-1][0])

    async def test_real_aero_and_map_parameter_renderers_keep_forecast_dates(self):
        import telegram_bot as bot
        from telegram_product_wizard import params_keyboard
        for product in ("aero", "map"):
            state = {"product": product, "mode": "single", "lead": 24, "point": {"lat": 55.75, "lon": 37.62, "label": "Москва"}}
            with patch.object(bot, "latest_available_run_for_lead", return_value=GfsRun("20261231", "18")):
                await bot._prepare_wizard_forecast(state)
            self.assertIn("01.01.2027 18:00 UTC", bot.params_text(state))
            buttons = [button for row in params_keyboard(state).inline_keyboard for button in row]
            self.assertTrue(any("+24 · 01.01 18:00" in button.text for button in buttons))


if __name__ == "__main__":
    unittest.main()
