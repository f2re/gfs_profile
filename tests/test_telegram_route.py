from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from geocode import GeoPoint
from telegram_route import (
    ROUTE_LONG_POINT_WARNING,
    _repeat_command,
    _resolve_endpoint,
    parse_route_request,
    route_settings_keyboard,
    route_settings_text,
)


class TelegramRouteTests(unittest.TestCase):
    def test_parse_route_request(self) -> None:
        parsed = parse_route_request("Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro")
        self.assertEqual(parsed.origin_query, "Москва")
        self.assertEqual(parsed.destination_query, "Санкт-Петербург")
        self.assertEqual(parsed.departure_lead, 24)
        self.assertEqual(parsed.speed_kmh, 300)
        self.assertEqual(parsed.spatial_step_km, 50)
        self.assertTrue(parsed.step_explicit)
        self.assertEqual(parsed.mode, "pro")

    def test_parse_coordinate_route(self) -> None:
        parsed = parse_route_request("55.75 37.62 → 59.94 30.31 +6 speed=450 mode=simple")
        self.assertEqual(parsed.origin_query, "55.75 37.62")
        self.assertEqual(parsed.destination_query, "59.94 30.31")
        self.assertEqual(parsed.departure_lead, 6)
        self.assertEqual(parsed.speed_kmh, 450)
        self.assertEqual(parsed.spatial_step_km, 25)
        self.assertFalse(parsed.step_explicit)

    def test_rejects_missing_separator(self) -> None:
        with self.assertRaises(ValueError):
            parse_route_request("Москва Санкт-Петербург +24")

    def test_resolver_preserves_user_place_name(self) -> None:
        candidate = GeoPoint(55.7558, 37.6173, "Москва, Россия", "nominatim")
        with patch("telegram_route.search_location_candidates", return_value=[candidate]):
            resolved = asyncio.run(_resolve_endpoint("Москва"))
        self.assertEqual(resolved.label, "Москва")
        self.assertEqual(resolved.lat, candidate.lat)
        self.assertEqual(resolved.lon, candidate.lon)

    def test_resolver_preserves_full_coordinate_pair(self) -> None:
        candidate = GeoPoint(55.7558, 37.6173, "55.7558, 37.6173", "coordinates")
        with patch("telegram_route.search_location_candidates", return_value=[candidate]):
            resolved = asyncio.run(_resolve_endpoint("55.7558, 37.6173"))
        self.assertEqual(resolved.label, "55.7558 37.6173")

    def test_repeat_command_preserves_place_names_and_grid(self) -> None:
        parsed = parse_route_request("Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro")
        data = type("Data", (), {
            "origin": GeoPoint(55.7558, 37.6173, "Москва", "nominatim"),
            "destination": GeoPoint(59.9386, 30.3141, "Санкт-Петербург", "nominatim"),
            "departure_lead": 24,
            "speed_kmh": 300,
            "mode": "pro",
            "run": type("Run", (), {"date": "20260710", "cycle": "06"})(),
        })()
        command = _repeat_command(data, parsed)
        self.assertIn("Москва -> Санкт-Петербург", command)
        self.assertIn("step=50", command)
        self.assertNotIn("55.7558", command)

    def test_long_route_settings_warn_and_offer_grid_choice(self) -> None:
        state = {
            "step": "settings",
            "origin": {"lat": 55.7558, "lon": 37.6173, "label": "Москва", "source": "test"},
            "destination": {"lat": 55.0084, "lon": 82.9357, "label": "Новосибирск", "source": "test"},
            "lead": 6,
            "speed": 300,
            "mode": "simple",
            "spatial_step": 25,
        }
        text = route_settings_text(state)
        self.assertIn("Москва → Новосибирск", text)
        self.assertIn("2813 км", text)
        self.assertIn("25 км", text)
        self.assertIn("114", text)
        self.assertTrue(
            "Долгий расчёт" in text or "Расчёт может занять" in text,
            msg=f"Нет предупреждения о длительном расчёте:\n{text}",
        )
        self.assertIn("50 км", text)
        self.assertIn("100 км", text)

        keyboard = route_settings_keyboard(state)
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("route:grid:25", callbacks)
        self.assertIn("route:grid:50", callbacks)
        self.assertIn("route:grid:100", callbacks)
        self.assertGreater(ROUTE_LONG_POINT_WARNING, 0)


if __name__ == "__main__":
    unittest.main()
