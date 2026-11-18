from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from geocode import GeoPoint
import telegram_saved_recipes as saved
import telegram_user_state


class TelegramSavedRecipeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prefs.sqlite3"
        self.path_patch = patch.object(telegram_user_state, "DEFAULT_DB_PATH", self.db)
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def point(label="Краснодар"):
        return GeoPoint(45.0355, 38.9753, label, "test")

    def test_result_keyboard_targets_exact_latest_recipe(self) -> None:
        recipe = saved._store().record_success(
            "telegram",
            100,
            "map",
            {"mode": "gif", "from": 0, "to": 48, "time_step": 3},
            self.point(),
        )
        markup = saved._result_actions_keyboard(recipe, include_schedule=True)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn(f"recipe:schedule:{recipe.recipe_id}", callbacks)
        self.assertIn(f"recipe:toggle:{recipe.recipe_id}", callbacks)
        self.assertIn(f"recipe:run:{recipe.recipe_id}", callbacks)
        self.assertIn(f"recipe:edit:{recipe.recipe_id}", callbacks)

    def test_pinned_recipe_button_changes_label(self) -> None:
        recipe = saved._store().record_success(
            "telegram",
            100,
            "profile",
            {"lead": 48},
            self.point(),
        )
        saved._store().set_pinned("telegram", 100, recipe.recipe_id, True)
        markup = saved._result_actions_keyboard(
            saved._store().get("telegram", 100, recipe.recipe_id),
            include_schedule=False,
        )
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("★ Открепить", labels)
        self.assertNotIn("🕒 В расписание", labels)

    def test_same_product_can_have_two_quick_scenarios(self) -> None:
        first = saved._store().record_success(
            "telegram",
            100,
            "map",
            {"mode": "gif", "from": 0, "to": 48, "time_step": 3},
            self.point("Краснодар"),
        )
        second = saved._store().record_success(
            "telegram",
            100,
            "map",
            {"mode": "gif", "from": 0, "to": 96, "time_step": 6},
            GeoPoint(55.75, 37.62, "Москва", "test"),
        )
        quick = saved._store().quick("telegram", 100, limit=2)
        self.assertEqual({item.recipe_id for item in quick}, {first.recipe_id, second.recipe_id})


if __name__ == "__main__":
    unittest.main()
