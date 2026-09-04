from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from messenger.user_recipes import UserRecipeStore


class MessengerUserRecipeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = UserRecipeStore(Path(self.tmp.name) / "recipes.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def point(label="Москва"):
        return SimpleNamespace(lat=55.75, lon=37.62, label=label, source="test")

    def test_same_recipe_is_deduplicated_and_counts_successes(self) -> None:
        first = self.store.record_success("telegram", 1, "profile", {"lead": 24, "run": "old"}, self.point())
        second = self.store.record_success("telegram", 1, "profile", {"lead": 24, "run": "new"}, self.point("Москва центр"))
        self.assertEqual(first.recipe_id, second.recipe_id)
        self.assertEqual(second.success_count, 2)
        self.assertNotIn("run", second.params)

    def test_distinct_states_of_one_product_remain_separate(self) -> None:
        a = self.store.record_success("telegram", 1, "map", {"mode": "gif", "to": 48, "time_step": 3}, self.point())
        b = self.store.record_success("telegram", 1, "map", {"mode": "gif", "to": 96, "time_step": 6}, self.point())
        self.assertNotEqual(a.recipe_id, b.recipe_id)
        quick = self.store.quick("telegram", 1, limit=2)
        self.assertEqual({item.recipe_id for item in quick}, {a.recipe_id, b.recipe_id})

    def test_pinned_recipe_has_quick_priority(self) -> None:
        old = self.store.record_success("max", "42", "profile", {"lead": 48}, self.point("Старая"))
        recent = self.store.record_success("max", "42", "profile", {"lead": 24}, self.point("Новая"))
        self.store.set_pinned("max", "42", old.recipe_id, True)
        quick = self.store.quick("max", "42", limit=2)
        self.assertEqual(quick[0].recipe_id, old.recipe_id)
        self.assertEqual(quick[1].recipe_id, recent.recipe_id)
        self.assertEqual(self.store.default_for_product("max", "42", "profile").recipe_id, old.recipe_id)

    def test_route_endpoint_labels_do_not_change_signature(self) -> None:
        params_a = {
            "origin": {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "a"},
            "destination": {"lat": 59.94, "lon": 30.31, "label": "Санкт-Петербург", "source": "a"},
            "lead": 24,
        }
        params_b = {
            "origin": {"lat": 55.75, "lon": 37.62, "label": "Москва центр", "source": "b"},
            "destination": {"lat": 59.94, "lon": 30.31, "label": "СПб", "source": "b"},
            "lead": 24,
        }
        first = self.store.record_success("telegram", 1, "route", params_a, self.point())
        second = self.store.record_success("telegram", 1, "route", params_b, self.point("Москва центр"))
        self.assertEqual(first.recipe_id, second.recipe_id)
        self.assertEqual(second.success_count, 2)

    def test_newly_pinned_recipe_becomes_product_default(self) -> None:
        first = self.store.record_success("max", "42", "profile", {"lead": 24}, self.point("A"))
        second = self.store.record_success("max", "42", "profile", {"lead": 48}, self.point("B"))
        self.store.set_pinned("max", "42", first.recipe_id, True)
        self.store.set_pinned("max", "42", second.recipe_id, True)
        self.assertEqual(self.store.default_for_product("max", "42", "profile").recipe_id, second.recipe_id)

    def test_platforms_are_isolated(self) -> None:
        max_recipe = self.store.record_success("max", "42", "profile", {"lead": 24}, self.point())
        vk_recipe = self.store.record_success("vk", "42", "profile", {"lead": 24}, self.point())
        self.assertNotEqual(max_recipe.recipe_id, vk_recipe.recipe_id)
        self.assertEqual(len(self.store.list("max", "42")), 1)
        self.assertEqual(len(self.store.list("vk", "42")), 1)


if __name__ == "__main__":
    unittest.main()
