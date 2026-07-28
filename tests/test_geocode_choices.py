from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from geocode import GeoPoint, GeocodeError
from geocode_choices import configured_geocoder_providers, search_location_candidates


class GeocodeChoicesTests(unittest.TestCase):
    def test_dadata_is_primary_by_default(self) -> None:
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata,local,nominatim"}, clear=False):
            self.assertEqual(configured_geocoder_providers()[0], "dadata")

    def test_coordinates_do_not_call_external_provider(self) -> None:
        with patch("geocode_choices.search_dadata") as dadata:
            points = search_location_candidates("55.75 37.62", 1)
        self.assertEqual(points[0].source, "coordinates")
        dadata.assert_not_called()

    def test_dadata_result_wins_before_local_and_nominatim(self) -> None:
        dadata_point = GeoPoint(55.75, 37.62, "г Москва", "dadata")
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata,local,nominatim"}, clear=False):
            with patch("geocode_choices.read_cached", return_value=None), patch(
                "geocode_choices.search_dadata", return_value=[dadata_point]
            ), patch("geocode_choices.local_lookup") as local, patch("geocode_choices._search_nominatim") as nominatim:
                points = search_location_candidates("Москва", 3)
        self.assertEqual(points, [dadata_point])
        local.assert_not_called()
        nominatim.assert_not_called()

    def test_nominatim_is_used_as_configured_fallback(self) -> None:
        fallback = GeoPoint(59.93, 30.31, "Санкт-Петербург", "nominatim")
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata,nominatim"}, clear=False):
            with patch("geocode_choices.read_cached", return_value=None), patch(
                "geocode_choices.search_dadata", side_effect=GeocodeError("DaData временно недоступна")
            ), patch("geocode_choices._search_nominatim", return_value=[fallback]):
                points = search_location_candidates("Санкт-Петербург", 1)
        self.assertEqual(points, [fallback])

    def test_old_nominatim_cache_does_not_bypass_dadata(self) -> None:
        cached = GeoPoint(55.75, 37.62, "старый кэш", "nominatim")
        fresh = GeoPoint(55.75, 37.62, "г Москва", "dadata")
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata,nominatim"}, clear=False):
            with patch("geocode_choices.read_cached", return_value=cached), patch(
                "geocode_choices.search_dadata", return_value=[fresh]
            ):
                points = search_location_candidates("Москва", 1)
        self.assertEqual(points, [fresh])

    def test_count_one_dadata_suggestion_is_not_cached_without_confirmation(self) -> None:
        point = GeoPoint(55.75, 37.62, "г Москва", "dadata")
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata"}, clear=False):
            with patch("geocode_choices.read_cached", return_value=None), patch(
                "geocode_choices.search_dadata", return_value=[point]
            ), patch("geocode_choices.write_cached") as write_cached:
                self.assertEqual(search_location_candidates("Москва", 1), [point])
        write_cached.assert_not_called()

    def test_unique_dadata_result_from_choice_search_is_cached(self) -> None:
        point = GeoPoint(55.75, 37.62, "г Москва", "dadata")
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata"}, clear=False):
            with patch("geocode_choices.read_cached", return_value=None), patch(
                "geocode_choices.search_dadata", return_value=[point]
            ), patch("geocode_choices.write_cached") as write_cached:
                self.assertEqual(search_location_candidates("Москва", 5), [point])
        write_cached.assert_called_once_with("Москва", point)

    def test_unknown_provider_is_rejected(self) -> None:
        with patch.dict(os.environ, {"GEOCODER_PROVIDERS": "dadata,unknown"}, clear=False):
            with self.assertRaises(GeocodeError):
                configured_geocoder_providers()


if __name__ == "__main__":
    unittest.main()
