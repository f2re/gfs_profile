from __future__ import annotations

import unittest

from telegram_schedule_route_compat import _route_spec, install


class TelegramScheduleRouteCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install()

    def test_route_spec_keeps_endpoints_and_drops_run(self) -> None:
        state = {
            "origin": {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "test"},
            "destination": {"lat": 59.94, "lon": 30.31, "label": "Санкт-Петербург", "source": "test"},
            "lead": 24,
            "speed": 300,
            "mode": "pro",
            "spatial_step": 50,
            "run": "20260901/00",
        }
        spec = _route_spec(state)
        self.assertEqual(spec["product"], "route")
        self.assertEqual(spec["point"]["label"], "Москва")
        self.assertEqual(spec["params"]["destination"]["label"], "Санкт-Петербург")
        self.assertEqual(spec["params"]["lead"], 24)
        self.assertEqual(spec["params"]["mode"], "pro")
        self.assertNotIn("run", spec["params"])

    def test_native_telegram_product_keyboard_includes_route(self) -> None:
        import telegram_schedules as schedules

        markup = schedules._product_keyboard()
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("sched:product:route", callbacks)

    def test_title_and_summary_support_route(self) -> None:
        import telegram_schedules as schedules

        spec = {
            "product": "route",
            "point": {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "test"},
            "params": {
                "origin": {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "test"},
                "destination": {"lat": 59.94, "lon": 30.31, "label": "Санкт-Петербург", "source": "test"},
                "lead": 24,
                "speed": 300,
                "mode": "simple",
                "spatial_step": 50,
            },
        }
        self.assertEqual(schedules._product_title("route"), "✈ Маршрут")
        summary = schedules._params_summary(spec)
        self.assertIn("Москва → Санкт-Петербург", summary)
        self.assertIn("сетка 50 км", summary)


if __name__ == "__main__":
    unittest.main()
