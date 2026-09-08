"""WN3 mean is a view of ensemble statistics, not a new deterministic model."""
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from packaging.requirements import Requirement

from meteogram_models import source_for_id, sources_by_kind
from meteogram_fetch import fetch_meteogram
from weathernext3_meteogram import fetch_weathernext3_meteogram
from weathernext3_zarr import WeatherNext3ZarrProvider
from weathernext3_provider import WeatherNext3Error
from test_weathernext3_meteogram import FakeProvider
from messenger.weathernext3_service import parse_weathernext3_input
from messenger.weathernext3_router import KIND_BUTTONS, _card_text, _card_keyboard, _options_keyboard
from messenger.meteogram_service import format_meteogram_summary
from wn3_fixtures import POINT

class ViewTests(unittest.TestCase):
    def test_model_picker_has_mean_and_ensemble_views(self):
        self.assertIn(source_for_id('wn3_mean'), sources_by_kind(False))
        self.assertIn(source_for_id('wn3'), sources_by_kind(True))
        self.assertIn('среднее ансамбля', source_for_id('wn3_mean').model)

    def test_same_mean_values_without_fabricating_member_counts(self):
        mean = fetch_weathernext3_meteogram('T',55.75,37.62,1,provider=FakeProvider(),view='mean')
        ensemble = fetch_weathernext3_meteogram('T',55.75,37.62,1,provider=FakeProvider(),view='ensemble')
        for key in mean.fields:
            np.testing.assert_allclose(mean.fields[key], ensemble.fields[key], equal_nan=True)
        self.assertEqual(mean.stats,{})
        self.assertTrue(ensemble.stats)
        self.assertFalse(mean.source.ensemble)
        self.assertTrue(ensemble.source.ensemble)
        self.assertIsNone(mean.member_count)
        self.assertEqual(mean.init_time_utc,ensemble.init_time_utc)
        summary=format_meteogram_summary(mean,'png')
        self.assertNotIn('Ансамблевая метеограмма',summary)
        self.assertIn('среднее ансамбля',summary)
        self.assertNotIn('64/64',summary)

    def test_fetch_routes_both_views_to_native_provider(self):
        with patch('weathernext3_meteogram.fetch_weathernext3_meteogram') as fetch:
            fetch_meteogram('wn3_mean','T',55.75,37.62,1)
            self.assertEqual(fetch.call_args.kwargs['view'],'mean')
            fetch_meteogram('wn3','T',55.75,37.62,1)
            self.assertEqual(fetch.call_args.kwargs['view'],'ensemble')

    def test_both_views_support_days_and_reports_in_common_ui(self):
        self.assertTrue({'meteogram','ensemble'} <= dict(KIND_BUTTONS).keys())
        for kind in ('meteogram','ensemble'):
            request=parse_weathernext3_input(f'Москва kind={kind} days=5 format=pdf')
            self.assertEqual(request.params['format'],'pdf')
            buttons=[b for row in _card_keyboard(request.params).rows for b in row]
            self.assertTrue(any('сут' in b.text for b in buttons))
            buttons=[b for row in _options_keyboard(request.params).rows for b in row]
            self.assertTrue(any(b.text == 'PDF' for b in buttons))
        self.assertIn('без полос разброса',_card_text(POINT,{'kind':'meteogram'}))
        self.assertIn('p10/p25',_card_text(POINT,{'kind':'ensemble'}))

    def test_both_direct_meteogram_views_share_wn3_capacity_limit(self):
        from messenger.meteogram_service import build_meteogram_product_result
        for source_id in ('weathernext3', 'weathernext3_mean'):
            with self.subTest(source=source_id), \
                 patch('messenger.runtime_resources.get_runtime_resources') as resources, \
                 patch('messenger.meteogram_service.fetch_meteogram') as fetch:
                wrapper = resources.return_value.wrap_blocking_weathernext3
                wrapper.return_value.side_effect = RuntimeError('test stopped before rendering')
                with self.assertRaisesRegex(RuntimeError, 'test stopped'):
                    build_meteogram_product_result(POINT, source_id, 1)
                wrapper.assert_called_once_with(fetch)

    def test_invalid_view_fails_before_cloud_call(self):
        with self.assertRaises(ValueError):
            fetch_weathernext3_meteogram('T',55.75,37.62,1,view='invented')

    def test_python310_receives_actionable_upper_air_error(self):
        with patch('weathernext3_zarr.sys.version_info',(3,10,21)):
            with self.assertRaisesRegex(WeatherNext3Error,r'Python 3\.11'):
                WeatherNext3ZarrProvider.from_env()

    def test_zarr_requirements_do_not_break_python310(self):
        path=Path(__file__).resolve().parents[1]/'requirements-weathernext3.txt'
        requirements=[Requirement(line) for line in path.read_text().splitlines() if line.strip() and not line.startswith('#')]
        for requirement in requirements:
            self.assertIsNotNone(requirement.marker)
            self.assertFalse(requirement.marker.evaluate({'python_version':'3.10'}))
            self.assertTrue(requirement.marker.evaluate({'python_version':'3.11'}))

if __name__=='__main__':
    unittest.main()
