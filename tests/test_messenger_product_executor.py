from __future__ import annotations

import unittest
from unittest.mock import patch

from messenger.contracts import CommonProductResult
from messenger.product_executor import ProductSnapshot, build_snapshot_result


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat, self.lon, self.label, self.source = lat, lon, label, "test"


class ProductExecutorTests(unittest.TestCase):
    def test_profile_snapshot_never_passes_saved_run(self) -> None:
        snapshot = ProductSnapshot.from_values("profile", Point(), {"lead": 24, "run": "20260901/00"})
        self.assertNotIn("run", snapshot.params)
        result = CommonProductResult("profile", "ok", [], {})
        with patch("messenger.product_executor.build_profile_product", return_value=result) as builder:
            actual = build_snapshot_result(snapshot)
        self.assertIs(actual, result)
        self.assertIsNone(builder.call_args.args[2])

    def test_route_snapshot_uses_embedded_endpoints(self) -> None:
        params = {
            "origin": {"lat": 55.75, "lon": 37.62, "label": "Москва", "source": "test"},
            "destination": {"lat": 59.94, "lon": 30.31, "label": "Санкт-Петербург", "source": "test"},
            "lead": 24,
            "speed": 300,
            "mode": "simple",
            "spatial_step": 50,
            "cycle": "00",
        }
        snapshot = ProductSnapshot.from_values("route", params["origin"], params)
        result = CommonProductResult("route", "ok", [], {})
        with patch("messenger.product_executor.build_route_product_result", return_value=result) as builder:
            actual = build_snapshot_result(snapshot)
        self.assertIs(actual, result)
        self.assertEqual(builder.call_args.args[0].label, "Москва")
        self.assertEqual(builder.call_args.args[1].label, "Санкт-Петербург")
        self.assertIsNone(builder.call_args.args[6])


if __name__ == "__main__":
    unittest.main()
