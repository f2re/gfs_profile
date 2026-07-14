from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from aero_plot import (
    DEFAULT_AERO_DIAGRAM,
    INDEX_CARD_RECTS,
    MAIN_CURVE_COLORS,
    SUPPORTED_AERO_DIAGRAMS,
    _frost_point_curve,
)


class AeroSingleModeTests(unittest.TestCase):
    def test_only_skewt_is_supported(self) -> None:
        self.assertEqual(SUPPORTED_AERO_DIAGRAMS, {"skewt"})
        self.assertEqual(DEFAULT_AERO_DIAGRAM, "skewt")

    def test_curve_color_contract(self) -> None:
        self.assertEqual(MAIN_CURVE_COLORS["temperature"], "#C62828")
        self.assertEqual(MAIN_CURVE_COLORS["parcel"], "#111827")
        self.assertNotEqual(MAIN_CURVE_COLORS["temperature"], MAIN_CURVE_COLORS["parcel"])

    def test_index_cards_do_not_overlap(self) -> None:
        for index, (x, y, width, height) in enumerate(INDEX_CARD_RECTS):
            self.assertGreaterEqual(x, 0.0)
            self.assertGreaterEqual(y, 0.0)
            self.assertLessEqual(x + width, 1.0)
            self.assertLessEqual(y + height, 1.0)
            for other in INDEX_CARD_RECTS[index + 1 :]:
                ox, oy, ow, oh = other
                overlap = not (
                    x + width <= ox or ox + ow <= x or y + height <= oy or oy + oh <= y
                )
                self.assertFalse(overlap)

    def test_ice_saturation_curve_is_only_below_freezing(self) -> None:
        frame = pd.DataFrame(
            {
                "temperature_c": [5.0, -5.0, -15.0],
                "dewpoint_c": [2.0, -7.0, -18.0],
            }
        )
        frost = _frost_point_curve(frame)
        self.assertTrue(np.isnan(frost[0]))
        self.assertTrue(np.isfinite(frost[1:]).all())

    def test_application_does_not_register_skewt_command(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TESTTOKEN"}, clear=False):
            import telegram_bot

            application = telegram_bot.build_application()
        commands = {
            command
            for handlers in application.handlers.values()
            for handler in handlers
            for command in (getattr(handler, "commands", ()) or ())
        }
        self.assertIn("aero", commands)
        self.assertNotIn("skewt", commands)


if __name__ == "__main__":
    unittest.main()
