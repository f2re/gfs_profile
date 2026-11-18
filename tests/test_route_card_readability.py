from __future__ import annotations

import unittest

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from route_profile_card_policy import assess_group, draw_professional_cards, draw_simple_cards, route_card_groups
from route_profile_render_common import prepare_figure
import test_route_profile_render_png as fixtures


class RouteCardReadabilityTests(unittest.TestCase):
    def test_long_route_cards_keep_every_segment_and_fit_their_panels(self):
        fixture = fixtures.RouteProfileRenderPngTests()
        cases = ((mode, draw, peak) for mode, draw in (
            ("simple", draw_simple_cards), ("pro", draw_professional_cards)
        ) for peak in (False, True))
        for mode, draw, local_peak in cases:
            with self.subTest(mode=mode, local_peak=local_peak):
                data = fixture._data(mode)
                if local_peak:
                    data.waypoints[0].risk_score = 3
                    data.waypoints[1].risk_score = 0
                    data.icing_score[:, :2] = 2
                groups = route_card_groups(data)
                before = [assess_group(data, group) for group in groups]
                fig, _, _, cards_ax = prepare_figure(data, professional=mode == "pro")
                try:
                    draw(cards_ax, data)
                    fig.canvas.draw()
                    renderer = fig.canvas.get_renderer()
                    panels = cards_ax.child_axes
                    self.assertEqual(len(panels), len(groups))
                    self.assertEqual(len(panels), 10)
                    widths = [panel.get_window_extent(renderer).width for panel in panels]
                    self.assertLess(max(widths) - min(widths), 1.0)
                    self.assertEqual(len({round(panel.get_position().y0, 4) for panel in panels}), 2)
                    for panel, group in zip(panels, groups):
                        area = panel.get_window_extent(renderer)
                        labels = [text.get_text() for text in panel.texts]
                        self.assertIn(f"{group.start_km:.0f}–{group.end_km:.0f} км", labels)
                        bounds = [text.get_window_extent(renderer) for text in panel.texts]
                        for bound in bounds:
                            self.assertGreaterEqual(bound.x0, area.x0 - 1)
                            self.assertLessEqual(bound.x1, area.x1 + 1)
                            self.assertGreaterEqual(bound.y0, area.y0 - 1)
                            self.assertLessEqual(bound.y1, area.y1 + 1)
                        for index, bound in enumerate(bounds):
                            for other in bounds[index + 1:]:
                                self.assertFalse(bound.overlaps(other), "Card text overlaps")
                    self.assertEqual(before, [assess_group(data, group) for group in groups])
                finally:
                    plt.close(fig)


if __name__ == "__main__":
    unittest.main()
