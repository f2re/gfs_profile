from __future__ import annotations

import unittest
import asyncio
from unittest.mock import Mock, patch

from forecast_menu import UNVERIFIED_TIME_TEXT, ensure_menu_run, menu_run
from forecast_time import forecast_lead_label, forecast_range_lines, forecast_time_lines, run_time_label
from gfs_core import GfsRun


class ForecastTimeTests(unittest.TestCase):
    def test_utc_rollover_and_long_lead_use_run_not_wall_clock(self):
        run = GfsRun("20261231", "18")
        self.assertEqual(run_time_label(run), "31.12.2026 18:00 UTC")
        self.assertEqual(forecast_lead_label(run, 6), "+6 · 01.01 00:00")
        self.assertIn("01.01.2027 18:00 UTC", forecast_time_lines(run, 24))
        self.assertIn("16.01.2027 18:00 UTC", forecast_time_lines(run, 384))

    def test_period_has_both_absolute_endpoints(self):
        text = forecast_range_lines(GfsRun("20261118", "12"), 0, 48)
        self.assertIn("18.11.2026 12:00 — 20.11.2026 12:00 UTC", text)
        self.assertIn("+0…+48", text)


class ForecastMenuCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_lookup_has_deadline_and_does_not_swallow_cancellation(self):
        async def slow(*args):
            await asyncio.Event().wait()
        state = {}
        with patch("forecast_menu.asyncio.to_thread", side_effect=slow), \
             patch("forecast_menu.MENU_RUN_TIMEOUT_SECONDS", 0.01):
            self.assertIsNone(await ensure_menu_run(state, 24, Mock()))
        self.assertEqual(state["_forecast_error"], UNVERIFIED_TIME_TEXT)
        with patch("forecast_menu.asyncio.to_thread", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await ensure_menu_run({}, 24, Mock())

    async def test_older_concurrent_lookup_cannot_replace_newer_selection(self):
        started, release = asyncio.Event(), asyncio.Event()
        recent, previous = GfsRun("20261118", "06"), GfsRun("20261118", "00")
        async def fake_thread(selector, lead):
            if lead == 24:
                started.set()
                await release.wait()
                return recent
            return previous
        state = {}
        with patch("forecast_menu.asyncio.to_thread", side_effect=fake_thread):
            old = asyncio.create_task(ensure_menu_run(state, 24, Mock()))
            await started.wait()
            self.assertEqual(await ensure_menu_run(state, 384, Mock()), previous)
            release.set()
            self.assertIsNone(await old)
        self.assertEqual(menu_run(state), previous)
        self.assertEqual(state["_forecast_lead"], 384)

    async def test_pagination_reuses_run_but_changed_lead_checks_availability(self):
        recent, previous = GfsRun("20261118", "06"), GfsRun("20261118", "00")
        selector = Mock(side_effect=lambda lead: recent if lead == 24 else previous)
        state = {}
        self.assertEqual(await ensure_menu_run(state, 24, selector), recent)
        self.assertEqual(await ensure_menu_run(state, 24, selector), recent)
        self.assertEqual(selector.call_count, 1)
        self.assertEqual(await ensure_menu_run(state, 384, selector), previous)
        self.assertEqual([item.args[0] for item in selector.call_args_list], [24, 384])
        self.assertEqual(menu_run(state), previous)

    async def test_unavailable_new_lead_never_reuses_stale_dates(self):
        selector = Mock(side_effect=[GfsRun("20261118", "06"), RuntimeError("unavailable")])
        state = {}
        await ensure_menu_run(state, 24, selector)
        self.assertIsNone(await ensure_menu_run(state, 384, selector))
        self.assertIsNone(menu_run(state))
        self.assertEqual(state["_forecast_error"], UNVERIFIED_TIME_TEXT)

    async def test_explicit_run_is_not_replaced_by_latest(self):
        run = GfsRun("20261117", "18")
        selector = Mock(side_effect=AssertionError("explicit cycle must be preserved"))
        self.assertEqual(await ensure_menu_run({}, 24, selector, explicit_run=run), run)
        selector.assert_not_called()

    async def test_cached_cycle_is_rechecked_after_ttl(self):
        selector = Mock(return_value=GfsRun("20261118", "00"))
        state = {}
        with patch("forecast_menu.monotonic", return_value=1000):
            await ensure_menu_run(state, 24, selector)
        with patch("forecast_menu.monotonic", return_value=1200):
            await ensure_menu_run(state, 24, selector)
        self.assertEqual(selector.call_count, 2)


if __name__ == "__main__":
    unittest.main()
