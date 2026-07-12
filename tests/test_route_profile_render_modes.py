from __future__ import annotations

import unittest

from route_profile_rendering import render_features


class RouteProfileRenderModesTests(unittest.TestCase):
    def test_simple_mode_is_visual_and_smoothed(self) -> None:
        features = render_features("simple")
        self.assertIn("dense_display_grid", features)
        self.assertIn("topology_preserving_smoothing", features)
        self.assertIn("continuous_temperature_gradient", features)
        self.assertIn("soft_cloud_masses", features)
        self.assertIn("vector_pictograms", features)
        self.assertIn("significant_hazard_fills", features)
        self.assertIn("sparse_wind_markers", features)
        self.assertIn("robust_segment_cards", features)
        self.assertNotIn("wind_barbs", features)
        self.assertNotIn("rh_80_90_contours", features)

    def test_professional_mode_is_academic(self) -> None:
        features = render_features("pro")
        self.assertIn("light_temperature_background", features)
        self.assertIn("rh_80_90_contours", features)
        self.assertIn("red_isotherms", features)
        self.assertIn("wind_isotachs", features)
        self.assertIn("wind_barbs", features)
        self.assertIn("discrete_hazard_hatching", features)
        self.assertIn("numeric_cards", features)
        self.assertIn("robust_segment_cards", features)
        self.assertNotIn("vector_pictograms", features)
        self.assertNotIn("soft_cloud_masses", features)

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            render_features("unknown")


if __name__ == "__main__":
    unittest.main()
