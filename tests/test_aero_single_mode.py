from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image

from aero_plot import (
    DEFAULT_AERO_DIAGRAM,
    INDEX_CARD_RECTS,
    MAIN_CURVE_COLORS,
    SUPPORTED_AERO_DIAGRAMS,
    _frost_point_curve,
)
from aero_plot_layout import (
    AERO_LAYOUT,
    BARB_MAX_COUNT,
    BARB_XLOC,
    FIGURE_SIZE,
    HODOGRAPH_LABEL_OFFSETS,
    _barb_indices,
    _plot_metpy_diagram,
    rectangles_overlap,
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

    def test_figure_panels_are_inside_canvas_and_do_not_overlap(self) -> None:
        items = list(AERO_LAYOUT.items())
        for name, (x, y, width, height) in items:
            with self.subTest(panel=name):
                self.assertGreaterEqual(x, 0.0)
                self.assertGreaterEqual(y, 0.0)
                self.assertGreater(width, 0.0)
                self.assertGreater(height, 0.0)
                self.assertLessEqual(x + width, 1.0)
                self.assertLessEqual(y + height, 1.0)
        for index, (name, rect) in enumerate(items):
            for other_name, other_rect in items[index + 1 :]:
                with self.subTest(first=name, second=other_name):
                    self.assertFalse(rectangles_overlap(rect, other_rect))

    def test_right_column_has_real_vertical_gutters(self) -> None:
        cards_bottom = AERO_LAYOUT["cards"][1]
        middle_top = AERO_LAYOUT["hazards"][1] + AERO_LAYOUT["hazards"][3]
        middle_bottom = AERO_LAYOUT["hazards"][1]
        lower_top = AERO_LAYOUT["hodograph"][1] + AERO_LAYOUT["hodograph"][3]
        self.assertGreaterEqual(cards_bottom - middle_top, 0.05)
        self.assertGreaterEqual(middle_bottom - lower_top, 0.05)

    def test_wind_barbs_stay_inside_main_axis_and_are_thinned(self) -> None:
        self.assertGreater(BARB_XLOC, 0.9)
        self.assertLessEqual(BARB_XLOC, 1.0)
        frame = pd.DataFrame({"pressure_hpa": np.linspace(1050.0, 50.0, 60)})
        indices = _barb_indices(frame)
        self.assertLessEqual(len(indices), BARB_MAX_COUNT)
        selected = frame.iloc[indices]["pressure_hpa"]
        self.assertTrue(((selected >= 100.0) & (selected <= 1000.0)).all())

    def test_hodograph_label_offsets_are_distinct(self) -> None:
        self.assertEqual(set(HODOGRAPH_LABEL_OFFSETS), {0, 1, 3, 6, 8})
        self.assertEqual(len(set(HODOGRAPH_LABEL_OFFSETS.values())), 5)

    def test_renderer_does_not_reflow_manual_layout_with_tight_bbox(self) -> None:
        self.assertNotIn('bbox_inches="tight"', inspect.getsource(_plot_metpy_diagram))

    def test_renderer_smoke_keeps_exact_canvas(self) -> None:
        pressure = np.array([1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100], dtype=float)
        height = np.array([100, 750, 1450, 3000, 4200, 5600, 7200, 9200, 10400, 11800, 14000, 16500], dtype=float)
        temperature = np.array([20, 16, 11, 1, -6, -15, -27, -42, -50, -57, -65, -72], dtype=float)
        dewpoint = temperature - np.array([5, 6, 5, 8, 7, 8, 10, 12, 13, 15, 16, 18], dtype=float)
        frame = pd.DataFrame(
            {
                "pressure_hpa": pressure,
                "temperature_c": temperature,
                "dewpoint_c": dewpoint,
                "u_wind_ms": np.linspace(2, 18, len(pressure)),
                "v_wind_ms": np.sin(np.linspace(0, 3, len(pressure))) * 10,
                "geopotential_height_m": height,
                "geopotential_height_km": height / 1000.0,
                "wind_speed_ms": np.linspace(3, 20, len(pressure)),
                "vertical_shear_ms_per_km": np.linspace(1, 8, len(pressure)),
                "relative_humidity_pct": np.full(len(pressure), 70.0),
            }
        )
        diagnostics = {
            "parcel": None,
            "sbcape": 0.0,
            "mlcape": None,
            "mucape": None,
            "sbcin": 0.0,
            "mlcin": None,
            "mucin": None,
            "lcl": None,
            "lfc": None,
            "el": None,
            "tt": 35.0,
            "k": 10.0,
        }
        result = SimpleNamespace(
            run=SimpleNamespace(date="20260714", cycle="00"),
            lead_hour=24,
            valid_time_utc=datetime(2026, 7, 15, 0, tzinfo=timezone.utc),
            requested_lat=59.93,
            requested_lon=30.316,
            grid_lat=60.0,
            grid_lon=30.25,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "aero.png"
            with (
                patch("aero_plot_layout.base._prepare_profile", return_value=frame),
                patch("aero_plot_layout.base._metpy_diagnostics", return_value=diagnostics),
                patch("aero_plot_layout.base._diagnose_layers", return_value=[]),
            ):
                _plot_metpy_diagram(result, output)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 10000)
            with Image.open(output) as image:
                expected_width = round(FIGURE_SIZE[0] * 180)
                expected_height = round(FIGURE_SIZE[1] * 180)
                self.assertLessEqual(abs(image.width - expected_width), 2)
                self.assertLessEqual(abs(image.height - expected_height), 2)

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
