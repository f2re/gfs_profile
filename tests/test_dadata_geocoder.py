from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from dadata_geocoder import search_dadata
from geocode import GeocodeError


class DadataGeocoderTests(unittest.TestCase):
    def test_requires_api_key(self) -> None:
        with patch.dict(os.environ, {"DADATA_API_KEY": ""}, clear=False):
            with self.assertRaises(GeocodeError):
                search_dadata("Москва", 1)

    def test_parses_coordinates_and_label(self) -> None:
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "suggestions": [
                {
                    "value": "г Москва",
                    "data": {
                        "geo_lat": "55.7558",
                        "geo_lon": "37.6173",
                        "city": "Москва",
                        "city_with_type": "г Москва",
                        "city_type_full": "город",
                        "region": "Москва",
                        "region_with_type": "г Москва",
                    },
                }
            ]
        }
        with patch.dict(os.environ, {"DADATA_API_KEY": "test-token"}, clear=False):
            with patch("dadata_geocoder.requests.post", return_value=response) as post:
                points = search_dadata("Москва", 1)
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0].lat, 55.7558)
        self.assertAlmostEqual(points[0].lon, 37.6173)
        self.assertEqual(points[0].source, "dadata")
        self.assertIn("Москва", points[0].label)
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Token test-token")

    def test_skips_suggestions_without_coordinates(self) -> None:
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {"suggestions": [{"value": "г Москва", "data": {"geo_lat": None, "geo_lon": None}}]}
        with patch.dict(os.environ, {"DADATA_API_KEY": "test-token"}, clear=False):
            with patch("dadata_geocoder.requests.post", return_value=response):
                self.assertEqual(search_dadata("Москва", 3), [])

    def test_403_has_actionable_error(self) -> None:
        response = Mock(status_code=403)
        with patch.dict(os.environ, {"DADATA_API_KEY": "bad-token"}, clear=False):
            with patch("dadata_geocoder.requests.post", return_value=response):
                with self.assertRaisesRegex(GeocodeError, "ключ|почта|лимит"):
                    search_dadata("Москва", 1)


if __name__ == "__main__":
    unittest.main()
