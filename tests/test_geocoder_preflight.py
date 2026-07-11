from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import geocoder_preflight
from geocode import GeoPoint


class GeocoderPreflightTests(unittest.TestCase):
    def test_missing_dadata_key_fails(self) -> None:
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata,local", "DADATA_API_KEY": ""}, clear=False):
            with patch("sys.argv", ["geocoder_preflight.py", "--no-network"]):
                self.assertEqual(geocoder_preflight.main(), 2)

    def test_nominatim_only_does_not_require_dadata_key(self) -> None:
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "local,nominatim", "DADATA_API_KEY": ""}, clear=False):
            with patch("sys.argv", ["geocoder_preflight.py", "--no-network"]):
                self.assertEqual(geocoder_preflight.main(), 0)

    def test_live_validation_result_is_checked(self) -> None:
        point = GeoPoint(55.75, 37.62, "г Москва", "dadata")
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata", "DADATA_API_KEY": "token"}, clear=False):
            with patch("geocoder_preflight.validate_dadata_access", return_value=point), patch(
                "sys.argv", ["geocoder_preflight.py"]
            ):
                self.assertEqual(geocoder_preflight.main(), 0)


if __name__ == "__main__":
    unittest.main()
