from __future__ import annotations

import unittest

import numpy as np
import xarray as xr

from cloudgram_product import (
    _cape_cin_180,
    _cb_score,
    _ceiling_agl,
    _hazard_score,
    cloudgram_leads,
)
from gfs_core import GfsProfileError


def _scalar_dataset(variable: str, value: float, *, short_name: str, type_of_level: str):
    array = xr.DataArray(
        np.asarray([[value]], dtype=float),
        dims=("latitude", "longitude"),
        coords={"latitude": [55.0], "longitude": [37.0]},
        attrs={
            "GRIB_shortName": short_name,
            "GRIB_typeOfLevel": type_of_level,
            "GRIB_stepType": "instant",
        },
    )
    return xr.Dataset({variable: array})


def _layer_dataset(variable: str, values, *, short_name: str):
    array = xr.DataArray(
        np.asarray(values, dtype=float).reshape(3, 1, 1),
        dims=("pressureFromGroundLayer", "latitude", "longitude"),
        coords={
            "pressureFromGroundLayer": [9000.0, 18000.0, 25500.0],
            "latitude": [55.0],
            "longitude": [37.0],
        },
        attrs={
            "GRIB_shortName": short_name,
            "GRIB_typeOfLevel": "pressureFromGroundLayer",
            "GRIB_stepType": "instant",
        },
    )
    return xr.Dataset({variable: array})


class CloudgramProductTests(unittest.TestCase):
    def test_default_leads_are_three_hourly_to_72(self) -> None:
        self.assertEqual(cloudgram_leads(0, 12, 3), [0, 3, 6, 9, 12])

    def test_cloudgram_is_limited_to_120_hours(self) -> None:
        with self.assertRaises(GfsProfileError):
            cloudgram_leads(0, 123, 3)

    def test_cb_score_is_capped_to_three(self) -> None:
        self.assertEqual(_cb_score(1500.0, -20.0, 2.0, 80.0, 10.0), 3)

    def test_cb_score_without_signals_is_zero(self) -> None:
        self.assertEqual(_cb_score(None, None, None, None, None), 0)

    def test_cb_score_normalizes_convective_accumulation_interval(self) -> None:
        one_hour = _cb_score(700.0, -80.0, 0.3, 40.0, 0.0, 1.0)
        three_hour = _cb_score(700.0, -80.0, 0.9, 40.0, 0.0, 3.0)
        self.assertEqual(one_hour, three_hour)
        self.assertEqual(one_hour, 2)

    def test_ceiling_is_converted_from_msl_to_agl(self) -> None:
        datasets = [
            _scalar_dataset("gh_ceiling", 850.0, short_name="gh", type_of_level="cloudCeiling"),
            _scalar_dataset("gh_surface", 250.0, short_name="gh", type_of_level="surface"),
        ]
        agl, ceiling_msl, surface = _ceiling_agl(datasets)
        self.assertEqual(agl, 600.0)
        self.assertEqual(ceiling_msl, 850.0)
        self.assertEqual(surface, 250.0)

    def test_ceiling_is_missing_without_surface_elevation(self) -> None:
        datasets = [_scalar_dataset("gh_ceiling", 850.0, short_name="gh", type_of_level="cloudCeiling")]
        agl, ceiling_msl, surface = _ceiling_agl(datasets)
        self.assertIsNone(agl)
        self.assertEqual(ceiling_msl, 850.0)
        self.assertIsNone(surface)

    def test_cape_cin_use_180_0_hpa_layer(self) -> None:
        datasets = [
            _scalar_dataset("cape_surface", 300.0, short_name="cape", type_of_level="surface"),
            _scalar_dataset("cin_surface", -20.0, short_name="cin", type_of_level="surface"),
            _layer_dataset("cape_layer", [400.0, 1200.0, 1500.0], short_name="cape"),
            _layer_dataset("cin_layer", [-200.0, -60.0, -30.0], short_name="cin"),
        ]
        cape, cin, layer = _cape_cin_180(datasets)
        self.assertEqual(cape, 1200.0)
        self.assertEqual(cin, -60.0)
        self.assertEqual(layer, "180–0 hPa AGL")

    def test_convective_potential_is_not_labelled_as_high_thunder_risk(self) -> None:
        score, text = _hazard_score(2, 0.5, 1500.0, 10.0, "RA", 1.0)
        self.assertEqual(score, 2)
        self.assertIn("конвективный потенциал", text)
        self.assertNotIn("гроза", text)

    def test_precipitation_hazard_is_interval_independent(self) -> None:
        one_hour, _ = _hazard_score(0, 3.5, 1500.0, 10.0, "RA", 1.0)
        three_hour, _ = _hazard_score(0, 10.5, 1500.0, 10.0, "RA", 3.0)
        self.assertEqual(one_hour, 2)
        self.assertEqual(three_hour, 2)

    def test_tsra_has_highest_local_cloudgram_score(self) -> None:
        score, text = _hazard_score(3, 2.0, 1500.0, 10.0, "TSRA", 1.0)
        self.assertEqual(score, 4)
        self.assertIn("модельная гроза", text)


if __name__ == "__main__":
    unittest.main()
