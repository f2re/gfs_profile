from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_user_state import (
    clear_product_preference,
    clear_user_data,
    default_product_params,
    get_active_location,
    get_last_success_preference,
    get_product_preference,
    get_quick_preferences,
    get_recent_locations,
    normalise_product_params,
    record_product_success,
    remember_location,
    save_product_selection,
    set_active_location,
)


class TelegramUserStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prefs.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def point(self, lat=55.75, lon=37.62, label="Москва"):
        return SimpleNamespace(lat=lat, lon=lon, label=label, source="test")

    def test_locations_survive_reopen_and_are_isolated(self):
        remember_location(1, self.point(), db_path=self.db)
        remember_location(2, self.point(59.94, 30.31, "Санкт-Петербург"), db_path=self.db)
        self.assertEqual(get_active_location(1, db_path=self.db).label, "Москва")
        self.assertEqual(get_active_location(2, db_path=self.db).label, "Санкт-Петербург")
        self.assertEqual([item.label for item in get_recent_locations(1, db_path=self.db)], ["Москва"])

    def test_duplicate_location_increments_and_becomes_recent(self):
        remember_location(1, self.point(), db_path=self.db)
        remember_location(1, self.point(59.94, 30.31, "СПб"), db_path=self.db)
        remember_location(1, self.point(55.751, 37.621, "Москва центр"), db_path=self.db)
        recent = get_recent_locations(1, db_path=self.db)
        self.assertEqual(recent[0].label, "Москва центр")
        self.assertEqual(recent[0].use_count, 2)
        self.assertEqual(len(recent), 2)

    def test_nonactivating_location_keeps_active_point(self):
        first = remember_location(1, self.point(), db_path=self.db)
        second = remember_location(
            1,
            self.point(59.94, 30.31, "СПб"),
            activate=False,
            db_path=self.db,
        )
        self.assertEqual(get_active_location(1, db_path=self.db).location_id, first.location_id)
        self.assertTrue(set_active_location(1, second.location_id, db_path=self.db))
        self.assertEqual(get_active_location(1, db_path=self.db).label, "СПб")

    def test_map_default_is_animation_for_48_hours(self):
        params = default_product_params("map")
        self.assertEqual(params["mode"], "gif")
        self.assertEqual((params["from"], params["to"], params["time_step"]), (0, 48, 3))
        self.assertEqual(((params["to"] - params["from"]) // params["time_step"]) + 1, 17)

    def test_long_map_period_gets_compatible_step(self):
        params = normalise_product_params("map", {"mode": "gif", "from": 0, "to": 96, "time_step": 3})
        self.assertEqual(params["time_step"], 6)
        self.assertLessEqual(((params["to"] - params["from"]) // params["time_step"]) + 1, 18)

    def test_map_variants_are_kept_independently(self):
        params = normalise_product_params(
            "map",
            {
                "mode": "single",
                "lead": 72,
                "variants": {
                    "gif": {"from": 0, "to": 48, "time_step": 3},
                    "single": {"lead": 24},
                },
            },
        )
        self.assertEqual(params["lead"], 72)
        self.assertEqual(params["variants"]["single"]["lead"], 72)
        self.assertEqual(params["variants"]["gif"]["to"], 48)
        self.assertEqual(params["variants"]["gif"]["time_step"], 3)

    def test_run_and_transient_keys_are_not_persisted(self):
        selected = save_product_selection(
            1,
            "map",
            {"mode": "gif", "from": 0, "to": 48, "time_step": 3, "run": "20260820/00", "candidates": [1]},
            self.point(),
            db_path=self.db,
        )
        self.assertNotIn("run", selected.params)
        self.assertNotIn("candidates", selected.params)

    def test_selection_and_success_are_separate(self):
        save_product_selection(1, "profile", {"lead": 48}, self.point(), db_path=self.db)
        selected = get_product_preference(1, "profile", db_path=self.db)
        self.assertEqual(selected.kind, "selected")
        self.assertEqual(selected.params["lead"], 48)
        self.assertIsNone(get_last_success_preference(1, db_path=self.db))

        record_product_success(1, "profile", {"lead": 24}, self.point(), db_path=self.db)
        success = get_last_success_preference(1, db_path=self.db)
        self.assertEqual(success.kind, "success")
        self.assertEqual(success.params["lead"], 24)

    def test_quick_actions_prefer_last_success_then_frequency(self):
        record_product_success(1, "map", {}, self.point(), db_path=self.db)
        record_product_success(1, "profile", {"lead": 24}, self.point(), db_path=self.db)
        record_product_success(1, "map", {}, self.point(), db_path=self.db)
        quick = get_quick_preferences(1, db_path=self.db)
        self.assertEqual(quick[0].product, "map")
        self.assertEqual(quick[0].success_count, 2)

    def test_reset_one_product_does_not_delete_location(self):
        remember_location(1, self.point(), db_path=self.db)
        record_product_success(1, "map", {}, self.point(), db_path=self.db)
        clear_product_preference(1, "map", db_path=self.db)
        self.assertIsNone(get_product_preference(1, "map", db_path=self.db))
        self.assertIsNotNone(get_active_location(1, db_path=self.db))

    def test_clear_user_data_is_complete(self):
        remember_location(1, self.point(), db_path=self.db)
        record_product_success(1, "map", {}, self.point(), db_path=self.db)
        clear_user_data(1, db_path=self.db)
        self.assertIsNone(get_active_location(1, db_path=self.db))
        self.assertIsNone(get_product_preference(1, "map", db_path=self.db))


if __name__ == "__main__":
    unittest.main()
