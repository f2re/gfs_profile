from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import telegram_result_copy


class TelegramResultCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        telegram_result_copy.install()

    def test_aero_caption_keeps_gfs_without_repeated_disclaimer(self) -> None:
        import telegram_aero

        result = SimpleNamespace(
            run=SimpleNamespace(date="20260712", cycle="12"),
            lead_hour=24,
            valid_time_utc=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
            grid_lat=55.75,
            grid_lon=37.62,
        )
        text = telegram_aero.format_aero_caption(result)
        self.assertIn("GFS", text)
        self.assertNotIn("не радиозонд", text.lower())

    def test_map_status_keeps_gfs_without_radar_phrase(self) -> None:
        import telegram_map

        data = {
            "run": SimpleNamespace(date="20260712", cycle="12"),
            "point": SimpleNamespace(label="Москва"),
            "lead_hour": 24,
        }
        text = telegram_map.format_map_status(data)
        self.assertIn("GFS", text)
        self.assertNotIn("не радар", text.lower())
        self.assertNotIn("не наблю", text.lower())

    def test_route_summary_does_not_duplicate_footer_warning(self) -> None:
        import telegram_route

        original = telegram_route.route_summary
        try:
            telegram_route.route_summary = lambda data: "строка\nℹ повторяющийся дисклеймер"
            # Reinstall on a fresh sentinel state to exercise wrapper behavior.
            import telegram_map

            telegram_map._RESULT_COPY_PATCHED = False
            telegram_result_copy.install()
            self.assertEqual(telegram_route.route_summary(object()), "строка")
        finally:
            telegram_route.route_summary = original


if __name__ == "__main__":
    unittest.main()
