from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from messenger.settings_router import SettingsRecipeStore
from messenger.user_locations import MessengerLocationStore


class Point:
    def __init__(self, lat: float, lon: float, label: str):
        self.lat = lat
        self.lon = lon
        self.label = label
        self.source = "test"


class MessengerLocationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.locations = MessengerLocationStore(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_platform_users_are_isolated(self) -> None:
        self.locations.remember("max", "42", Point(55.75, 37.62, "Москва"))
        self.locations.remember("vk", "42", Point(59.94, 30.31, "Санкт-Петербург"))
        self.assertEqual(self.locations.active("max", "42").label, "Москва")
        self.assertEqual(self.locations.active("vk", "42").label, "Санкт-Петербург")

    def test_nonactive_route_points_do_not_replace_active(self) -> None:
        active = self.locations.remember("max", "42", Point(55.75, 37.62, "Москва"))
        self.locations.remember("max", "42", Point(59.94, 30.31, "Санкт-Петербург"), activate=False)
        self.assertEqual(self.locations.active("max", "42").location_id, active.location_id)
        self.assertEqual(len(self.locations.recent("max", "42")), 2)

    def test_ensure_does_not_increment_use_count(self) -> None:
        item = self.locations.ensure("max", "42", Point(55.75, 37.62, "Москва"), used_at="2026-09-01T00:00:00Z")
        again = self.locations.ensure("max", "42", Point(55.75, 37.62, "Москва"), used_at="2026-09-02T00:00:00Z")
        self.assertEqual(item.use_count, 1)
        self.assertEqual(again.use_count, 1)
        self.assertEqual(again.last_used_at, "2026-09-02T00:00:00Z")

    def test_recipe_store_mirrors_point_product_and_route(self) -> None:
        recipes = SettingsRecipeStore(self.path, locations=self.locations)
        recipes.record_success("max", "42", "profile", {"lead": 24}, Point(55.75, 37.62, "Москва"))
        self.assertEqual(self.locations.active("max", "42").label, "Москва")

        route = {
            "origin": {"lat": 45.0, "lon": 39.0, "label": "Краснодар", "source": "test"},
            "destination": {"lat": 47.22, "lon": 39.7, "label": "Ростов-на-Дону", "source": "test"},
            "lead": 24,
            "speed": 300,
            "spatial_step": 50,
            "mode": "simple",
        }
        recipes.record_success("max", "42", "route", route, route["origin"])
        self.assertEqual(self.locations.active("max", "42").label, "Москва")
        labels = {item.label for item in self.locations.recent("max", "42", limit=10)}
        self.assertIn("Краснодар", labels)
        self.assertIn("Ростов-на-Дону", labels)


if __name__ == "__main__":
    unittest.main()
