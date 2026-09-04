from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from geocode import GeoPoint
from gfs_core import GfsProfileError
from telegram_aero import ParsedAeroRequest, parse_aero_request, resolve_aero_request, run_aero_product
from telegram_cloudgram import ParsedCloudgramRequest, parse_cloudgram_request, resolve_cloudgram_request, run_cloudgram_product
from telegram_map import ParsedMapRequest, parse_map_request, resolve_map_request, run_map_product
from telegram_product_wizard import (
    copy_command,
    params_keyboard,
    set_point,
    start_aero_wizard_state,
    start_cloudgram_wizard_state,
    start_map_wizard_state,
    start_windgram_wizard_state,
)
from telegram_route import ParsedRouteRequest, parse_route_request, run_route_product
from telegram_windgram import ParsedWindgramRequest, parse_windgram_request, resolve_windgram_request, run_windgram_product


class _Status:
    def __init__(self, initial_text: str) -> None:
        self.initial_text = initial_text
        self.edits: list[str] = []

    async def edit_text(self, text: str, *args, **kwargs) -> None:
        self.edits.append(text)


class _Message:
    def __init__(self) -> None:
        self.statuses: list[_Status] = []
        self.from_user = None

    async def reply_text(self, text: str, *args, **kwargs) -> _Status:
        status = _Status(text)
        self.statuses.append(status)
        return status


class TelegramFlowSmokeTests(unittest.TestCase):
    def _wizard_state(self, state: dict[str, object]) -> dict[str, object]:
        return set_point(
            state,
            {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "test"},
        )

    @staticmethod
    def _command_args(command: str) -> str:
        return command.split(" ", 1)[1]

    def test_composed_aero_caption_accepts_runtime_signature(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TESTTOKEN"}, clear=False):
            import telegram_bot  # noqa: F401
            import telegram_aero

        result = SimpleNamespace(
            run=SimpleNamespace(date="20260714", cycle="00"),
            lead_hour=24,
            valid_time_utc=datetime(2026, 7, 15, 0, tzinfo=timezone.utc),
            grid_lat=59.75,
            grid_lon=30.25,
        )
        caption = telegram_aero.format_aero_caption(result)
        self.assertIn("GFS", caption)
        self.assertIn("+24", caption)

    def test_application_registers_all_active_flows(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TESTTOKEN"}, clear=False):
            import telegram_bot

            application = telegram_bot.build_application()
        commands = {
            command
            for handlers in application.handlers.values()
            for handler in handlers
            for command in (getattr(handler, "commands", ()) or ())
        }
        for command in {"start", "help", "profile", "route", "aero", "windgram", "cloudgram", "map", "cycle", "status", "admin", "cancel"}:
            self.assertIn(command, commands)
        self.assertNotIn("skewt", commands)

    def test_wizard_commands_round_trip_through_parsers(self) -> None:
        aero_state = self._wizard_state(start_aero_wizard_state(24))
        aero_command = copy_command(aero_state)
        self.assertIsNotNone(aero_command)
        self.assertNotIn("type=", aero_command)
        self.assertEqual(parse_aero_request(self._command_args(aero_command), 24).diagram_type, "skewt")

        wind_state = self._wizard_state(start_windgram_wizard_state())
        wind_command = copy_command(wind_state)
        self.assertIsNotNone(wind_command)
        self.assertEqual(parse_windgram_request(self._command_args(wind_command)).param, "wind")

        cloud_state = self._wizard_state(start_cloudgram_wizard_state())
        cloud_command = copy_command(cloud_state)
        self.assertIsNotNone(cloud_command)
        self.assertEqual(parse_cloudgram_request(self._command_args(cloud_command)).mode, "pro")

        map_state = self._wizard_state(start_map_wizard_state(24))
        map_command = copy_command(map_state)
        self.assertIsNotNone(map_command)
        self.assertEqual(parse_map_request(self._command_args(map_command)).mode, "single")

        map_series = dict(map_state, mode="series", **{"from": 0, "to": 24, "time_step": 6})
        series_command = copy_command(map_series)
        self.assertIsNotNone(series_command)
        self.assertEqual(parse_map_request(self._command_args(series_command)).mode, "series")

        route = parse_route_request("Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro")
        self.assertEqual(route.mode, "pro")
        self.assertEqual(route.spatial_step_km, 50)

    def test_all_wizard_callbacks_have_supported_contract(self) -> None:
        states = [
            self._wizard_state(start_aero_wizard_state(24)),
            self._wizard_state(start_windgram_wizard_state()),
            self._wizard_state(start_cloudgram_wizard_state()),
            self._wizard_state(start_map_wizard_state(24)),
        ]
        allowed = (
            "wiz:aero:lead:",
            "wiz:wind:param:",
            "wiz:wind:to:",
            "wiz:wind:step:",
            "wiz:cloud:mode:",
            "wiz:cloud:to:",
            "wiz:cloud:step:",
            "wiz:map:mode:",
            "wiz:map:lead:",
            "wiz:map:from:",
            "wiz:map:to:",
            "wiz:map:step:",
            "wiz:map:basemap:",
            "wiz:run",
            "wiz:point",
            "wiz:cancel",
        )
        for state in states:
            callbacks = [
                button.callback_data
                for row in params_keyboard(state).inline_keyboard
                for button in row
            ]
            self.assertTrue(callbacks)
            for callback in callbacks:
                self.assertIsNotNone(callback)
                self.assertTrue(callback.startswith(allowed), callback)
                self.assertNotIn("wiz:aero:type:", callback)

    def test_invalid_direct_requests_return_false(self) -> None:
        async def check() -> None:
            semaphore = asyncio.Semaphore(1)
            message = _Message()
            self.assertFalse(await resolve_aero_request(message, "", 24, semaphore, semaphore))
            self.assertFalse(await resolve_windgram_request(message, "", semaphore, semaphore))
            self.assertFalse(await resolve_cloudgram_request(message, "", semaphore, semaphore))
            self.assertFalse(await resolve_map_request(message, "", semaphore, semaphore))
            self.assertGreaterEqual(len(message.statuses), 4)

        asyncio.run(check())

    def test_cycle_selection_failure_is_reported_in_every_product(self) -> None:
        point = GeoPoint(55.75, 37.62, "Москва", "test")

        async def check() -> None:
            semaphore = asyncio.Semaphore(1)
            cases = [
                (
                    "messenger.aero_service.latest_available_run_for_lead",
                    lambda message: run_aero_product(message, point, ParsedAeroRequest("Москва", 24, None), semaphore),
                ),
                (
                    "telegram_windgram.latest_available_run_for_lead",
                    lambda message: run_windgram_product(message, point, ParsedWindgramRequest("Москва", None, 0, 6, 3, 500, "wind"), semaphore),
                ),
                (
                    "telegram_cloudgram.latest_available_run_for_lead",
                    lambda message: run_cloudgram_product(message, point, ParsedCloudgramRequest("Москва", None, 0, 6, 3, "pro"), semaphore),
                ),
                (
                    "telegram_map.latest_available_run_for_lead",
                    lambda message: run_map_product(message, point, ParsedMapRequest("Москва", None, 24, 24, 3, False, 100.0, "single", "places"), semaphore),
                ),
            ]
            for target, runner in cases:
                message = _Message()
                with patch(target, side_effect=GfsProfileError("нет опубликованного цикла")):
                    self.assertFalse(await runner(message))
                self.assertTrue(message.statuses)
                self.assertIn("Ошибка: нет опубликованного цикла", message.statuses[0].edits)

            route_message = _Message()
            route = ParsedRouteRequest("Москва", "Тверь", 24, 300, "simple", None, 50, True)
            with (
                patch("telegram_route._route_plan", return_value=(100.0, 0.33, [(0, 0, 0, 0, 24)])),
                patch("telegram_route.latest_available_run_for_lead", side_effect=GfsProfileError("нет опубликованного цикла")),
                patch("telegram_route.record_request_start", return_value=1),
                patch("telegram_route.record_request_finish"),
            ):
                self.assertFalse(await run_route_product(route_message, point, GeoPoint(56.0, 36.0, "Тверь", "test"), route))
            self.assertIn("Ошибка: нет опубликованного цикла", route_message.statuses[0].edits)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
