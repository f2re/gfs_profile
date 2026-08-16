from __future__ import annotations

import unittest
from types import SimpleNamespace

import meteogram_pdf as pdf


class MeteogramPdfReadabilityTests(unittest.TestCase):
    def test_daily_table_hides_uniform_ensemble_column(self):
        rows = [
            SimpleNamespace(
                day=__import__("datetime").date(2026, 8, 16),
                weather="Осадки вероятны",
                temperature="+15,0…+20,0 °C\nq10-q90 +13,0…+22,0 °C",
                precipitation="2,4 мм за 15 ч\nсумма центрального ряда\nP≥0,1 мм/3 ч: 92 % (47/51)\nP≥1 мм/3 ч: 12 % (6/51)\nP≥5 мм/3 ч: 0 % (0/51)",
                wind="до 5,0 м/с", pressure="1000…1005 гПа",
                ensemble="51/51 членов\nустойчивый сигнал",
            )
            for _ in range(2)
        ]
        data = SimpleNamespace(daily_rows=rows)
        headers, values, *_ = pdf._daily_spec(data, True)
        self.assertNotIn("Ансамбль", headers)
        self.assertTrue(all(len(row) == 6 for row in values))
        precip = values[0][3]
        self.assertNotIn("центрального ряда", precip)
        self.assertNotIn("(47/51)", precip)
        self.assertNotIn("P≥5", precip)
        self.assertIn("P≥0,1/3 ч: 92 %", precip)
        self.assertIn("P≥1/3 ч: 12 %", precip)
        self.assertIn("q10–q90", values[0][2])

    def test_multiline_rows_get_real_height(self):
        height = pdf._row_heights([["a\nb\nc\nd\ne"]])[0]
        self.assertGreaterEqual(height, 0.07)

    def test_zero_median_is_not_worded_as_no_precip_when_probability_exists(self):
        value = pdf._compact_precipitation(
            "без существенных осадков\nP≥0,1 мм/3 ч: 33 % (17/51)",
            ensemble=True, control=True,
        )
        self.assertIn("медиана 0 мм", value)
        self.assertIn("P≥0,1/3 ч: 33 %", value)
        self.assertNotIn("без существенных", value)


if __name__ == "__main__":
    unittest.main()
