from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from forecast_menu import UNVERIFIED_TIME_TEXT
from geocode import GeoPoint
from gfs_core import GfsRun
from messenger.callback_codec import encode_callback
from messenger.contracts import NormalizedEvent
from messenger.map_router import MapMessengerRouter
from messenger.router import MessengerRouter, RouterDependencies
from messenger.state import FlowState
from messenger.user_recipes import UserRecipeStore
from tests.test_forecast_menu_flows import Gateway, Message


class SavedForecastLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")
        self.point = GeoPoint(55.75, 37.62, "Москва", "test")
        self.old, self.new = GfsRun("20261118", "00"), GfsRun("20261118", "06")

    def event(self, platform="max", recipe_id=1):
        return NormalizedEvent(platform, "click", "CALLBACK", "user", "chat", message_id="card",
                               callback_id="click", callback_payload=encode_callback("recipe", "run", recipe_id))

    async def test_all_dated_recipes_use_preview_run_then_announce_expired_refresh(self):
        cases = (
            ("profile", {"lead": 24}, "_send_recipe_card", "_run_profile"),
            ("aero", {"lead": 24}, "_send_aero_recipe_card", "_run_aero"),
            ("windgram", {"from": 0, "to": 120, "step": 6, "top": 500, "param": "wind"}, "_send_windgram_recipe_card", "_run_windgram"),
            ("cloudgram", {"from": 0, "to": 72, "step": 3, "mode": "pro"}, "_send_cloudgram_recipe_card", "_run_cloudgram"),
            ("map", {"mode": "single", "from": 24, "to": 24, "step": 6, "radius": 100, "basemap": "places"}, "_send_recipe", "_run"),
        )
        for platform in ("telegram", "max", "vk"):
            for product, params, preview_name, runner_name in cases:
                with self.subTest(platform=platform, product=product):
                    selector = Mock(side_effect=[self.old, self.new])
                    router = MapMessengerRouter(RouterDependencies(geocode=lambda *_: [], latest_run_for_lead=selector), recipes=self.store)
                    runner = AsyncMock()
                    setattr(router, runner_name, runner)
                    recipe = self.store.record_success(platform, "user", product, params, self.point)
                    event, gateway = self.event(platform, recipe.recipe_id), Gateway(platform)
                    await getattr(router, preview_name)(event, gateway, recipe)
                    state = router.sessions.get(platform, "user", "chat")
                    self.assertEqual(state.params["_forecast_recipe_id"], recipe.recipe_id)
                    await router.handle(event, gateway)
                    self.assertEqual(runner.await_args.args[-1], self.old)
                    self.assertEqual(selector.call_count, 1, "fresh preview must not silently choose a new cycle")
                    state.params["_forecast_checked"] = -1e9
                    before = len(gateway.calls)
                    await router.handle(event, gateway)
                    self.assertEqual(runner.await_args.args[-1], self.new)
                    self.assertEqual(selector.call_count, 2)
                    self.assertTrue(any("Цикл обновлён" in call[1] and "18.11.2026 06:00 UTC" in call[1]
                                        for call in gateway.calls[before:]))

    async def test_other_recipe_cannot_reuse_current_preview_cycle(self):
        selector = Mock(side_effect=[self.old, self.new])
        router = MapMessengerRouter(RouterDependencies(geocode=lambda *_: [], latest_run_for_lead=selector), recipes=self.store)
        first = self.store.record_success("max", "user", "profile", {"lead": 24}, self.point)
        second = self.store.record_success("max", "user", "profile", {"lead": 24}, GeoPoint(45, 39, "Краснодар", "test"))
        gateway = Gateway("max")
        await router._send_recipe_card(self.event(recipe_id=first.recipe_id), gateway, first)
        router._run_profile = AsyncMock()
        await router.handle(self.event(recipe_id=second.recipe_id), gateway)
        self.assertEqual(router._run_profile.await_args.args[-1], self.new)
        self.assertEqual(router._run_profile.await_args.args[2].label, "Краснодар")
        self.assertEqual(selector.call_count, 2)

    async def test_unavailable_refresh_does_not_launch_recipe_with_implicit_run(self):
        selector = Mock(side_effect=[self.old, RuntimeError("NOMADS unavailable")])
        router = MapMessengerRouter(RouterDependencies(geocode=lambda *_: [], latest_run_for_lead=selector), recipes=self.store)
        recipe = self.store.record_success("max", "user", "profile", {"lead": 24}, self.point)
        event, gateway = self.event(recipe_id=recipe.recipe_id), Gateway("max")
        await router._send_recipe_card(event, gateway, recipe)
        router.sessions.get("max", "user", "chat").params["_forecast_checked"] = -1e9
        router._run_profile = AsyncMock()
        await router.handle(event, gateway)
        router._run_profile.assert_not_awaited()
        self.assertTrue(any(UNVERIFIED_TIME_TEXT in call[1] for call in gateway.calls))

    async def test_cancel_during_saved_recipe_refresh_does_not_launch_or_restore_it(self):
        router = MapMessengerRouter(RouterDependencies(geocode=lambda *_: [], latest_run_for_lead=lambda _: self.old), recipes=self.store)
        recipe = self.store.record_success("max", "user", "profile", {"lead": 24}, self.point)
        event, gateway = self.event(recipe_id=recipe.recipe_id), Gateway("max")
        await router._send_recipe_card(event, gateway, recipe)
        started, release = asyncio.Event(), asyncio.Event()
        async def refresh(*args):
            started.set()
            await release.wait()
            return self.new
        router._run_profile = AsyncMock()
        with patch.object(router, "_forecast_menu_run", side_effect=refresh):
            task = asyncio.create_task(router.handle(event, gateway))
            await started.wait()
            await router.handle(NormalizedEvent("max", "cancel", "COMMAND", "user", "chat", text="/cancel", command="cancel"), gateway)
            release.set()
            await task
        router._run_profile.assert_not_awaited()
        self.assertIsNone(router.sessions.get("max", "user", "chat"))
        self.assertIn("Выбор сброшен", gateway.calls[-1][1])

    async def test_same_lead_double_click_never_launches_superseded_none(self):
        router = MessengerRouter(RouterDependencies(geocode=lambda *_: []))
        state = FlowState(step="choose_lead", point=self.point)
        router.sessions.set("max", "user", "chat", state)
        router._run_profile = AsyncMock()
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def check(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                return None
            return self.new
        event = NormalizedEvent("max", "1", "CALLBACK", "user", "chat", callback_id="1",
                                callback_payload=encode_callback("profile", "lead", 24))
        gateway = Gateway("max")
        with patch.object(router, "_forecast_menu_run", side_effect=check):
            old = asyncio.create_task(router.handle(event, gateway))
            await started.wait()
            await router.handle(event, gateway)
            release.set()
            await old
        router._run_profile.assert_awaited_once()
        self.assertEqual(router._run_profile.await_args.args[-1], self.new)


class TelegramWizardLaunchSafetyTests(unittest.IsolatedAsyncioTestCase):
    def state(self, product="aero"):
        return {"product": product, "mode": "single", "step": "params", "lead": 24,
                "point": {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "test"}}

    async def test_normal_composed_callback_still_runs_after_personal_copy(self):
        import telegram_bot as bot
        message, state = Message(), self.state()
        context = SimpleNamespace(user_data={bot.PRODUCT_WIZARD_KEY: state})
        query = SimpleNamespace(data="wiz:run", message=message, answer=AsyncMock(), edit_message_text=message.edit_text)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=0), effective_message=message)
        with patch.object(bot, "latest_available_run_for_lead", return_value=GfsRun("20261118", "06")), \
             patch.object(bot, "_tracked_product", new_callable=AsyncMock) as runner, \
             patch("telegram_personal_ux.save_product_selection"):
            await bot.product_wizard_callback(update, context)
        runner.assert_awaited_once()
        self.assertEqual(runner.await_args.kwargs["run"], GfsRun("20261118", "06"))
        self.assertNotIn(bot.PRODUCT_WIZARD_KEY, context.user_data)

    async def test_cancel_or_new_wizard_during_preflight_prevents_old_launch(self):
        import telegram_bot as bot
        for action in ("cancel", "new_wizard"):
            for product in ("aero", "map"):
                with self.subTest(action=action, product=product):
                    message, state = Message(), self.state(product)
                    context = SimpleNamespace(user_data={bot.PRODUCT_WIZARD_KEY: state})
                    started, release = asyncio.Event(), asyncio.Event()
                    async def check(active):
                        started.set()
                        await release.wait()
                        return GfsRun("20261118", "06")
                    replacement = self.state("cloudgram")
                    with patch.object(bot, "_prepare_wizard_forecast", side_effect=check), \
                         patch.object(bot, "_tracked_product", new_callable=AsyncMock) as runner:
                        task = asyncio.create_task(bot._run_wizard_product(message, context, state))
                        await started.wait()
                        if action == "cancel":
                            bot._clear_pending(context)
                        else:
                            context.user_data[bot.PRODUCT_WIZARD_KEY] = replacement
                        release.set()
                        await task
                    runner.assert_not_awaited()
                    if action == "cancel":
                        self.assertNotIn(bot.PRODUCT_WIZARD_KEY, context.user_data)
                    else:
                        self.assertIs(context.user_data[bot.PRODUCT_WIZARD_KEY], replacement)

    async def test_direct_saved_preference_registers_active_state_and_runs(self):
        import telegram_bot as bot
        import telegram_personal_ux as personal
        message = Message()
        context = SimpleNamespace(user_data={})
        update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=0))
        preference = SimpleNamespace(user_id=0, product="aero", params={"lead": 24})
        with patch.object(personal, "_preference_point", return_value=self.state()["point"]), \
             patch.object(bot, "latest_available_run_for_lead", return_value=GfsRun("20261118", "06")), \
             patch.object(bot, "_tracked_product", new_callable=AsyncMock) as runner:
            await personal._run_preference(update, context, vars(bot), preference)
        runner.assert_awaited_once()
        self.assertEqual(runner.await_args.kwargs["run"], GfsRun("20261118", "06"))

    async def test_failed_preflight_keeps_wizard_and_never_launches_none(self):
        import telegram_bot as bot
        message, state = Message(), self.state()
        context = SimpleNamespace(user_data={bot.PRODUCT_WIZARD_KEY: state})
        with patch.object(bot, "latest_available_run_for_lead", side_effect=RuntimeError("unavailable")), \
             patch.object(bot, "_tracked_product", new_callable=AsyncMock) as runner:
            await bot._run_wizard_product(message, context, state)
        runner.assert_not_awaited()
        self.assertIn(bot.PRODUCT_WIZARD_KEY, context.user_data)
        self.assertTrue(any(UNVERIFIED_TIME_TEXT in text for text, kwargs in message.calls))

    async def test_refresh_announces_new_cycle_before_wizard_launch(self):
        import telegram_bot as bot
        message, state = Message(), self.state()
        state.update(_forecast_run={"date": "20261118", "cycle": "00"}, _forecast_lead=24, _forecast_checked=-1e9)
        context = SimpleNamespace(user_data={bot.PRODUCT_WIZARD_KEY: state})
        with patch.object(bot, "latest_available_run_for_lead", return_value=GfsRun("20261118", "06")), \
             patch.object(bot, "_tracked_product", new_callable=AsyncMock) as runner:
            await bot._run_wizard_product(message, context, state)
        self.assertEqual(runner.await_args.kwargs["run"], GfsRun("20261118", "06"))
        self.assertTrue(any("Цикл обновлён" in text and "19.11.2026 06:00 UTC" in text for text, kwargs in message.calls))


if __name__ == "__main__":
    unittest.main()
