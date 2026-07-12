from __future__ import annotations

import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import geocoder_preflight
from geocode import GeoPoint


class GeocoderPreflightTests(unittest.TestCase):
    @staticmethod
    def _run_main(argv: list[str]) -> tuple[int, str]:
        output = StringIO()
        with patch("sys.argv", argv), redirect_stdout(output), redirect_stderr(output):
            code = geocoder_preflight.main()
        return code, output.getvalue()

    def test_missing_dadata_key_fails(self) -> None:
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata,local", "DADATA_API_KEY": ""}, clear=False):
            code, output = self._run_main(["geocoder_preflight.py", "--no-network"])
        self.assertEqual(code, 2)
        self.assertIn("DADATA_API_KEY is required", output)

    def test_nominatim_only_does_not_require_dadata_key(self) -> None:
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "local,nominatim", "DADATA_API_KEY": ""}, clear=False):
            code, output = self._run_main(["geocoder_preflight.py", "--no-network"])
        self.assertEqual(code, 0)
        self.assertIn("DaData disabled", output)

    def test_live_validation_result_is_checked(self) -> None:
        point = GeoPoint(55.75, 37.62, "г Москва", "dadata")
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata", "DADATA_API_KEY": "token"}, clear=False):
            with patch("geocoder_preflight.validate_dadata_access", return_value=point):
                code, output = self._run_main(["geocoder_preflight.py"])
        self.assertEqual(code, 0)
        self.assertIn("DaData OK", output)


if __name__ == "__main__":
    unittest.main()
