from __future__ import annotations

from wn3_test_support import enable_for_module as setUpModule
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import numpy as np
import xarray as xr

from wn3_fixtures import INIT, POINT, upper_dataset, upper_provider
from test_weathernext3_provider import FakeExecutor
from weathernext3_cache import cached_json
from weathernext3_cancel import cancellation, check_cancelled, Cancelled
from weathernext3_math import wind_from, exceedance_probability
from weathernext3_provider import WeatherNext3Provider, WeatherNext3Error, WeatherNext3Run, GoogleBigQueryExecutor
from weathernext3_zarr import WeatherNext3ZarrProvider, ZarrRun, select_hours, select_point
from weathernext3_surface import surface_rows
from messenger.weathernext3_service import _row_for_hour, normalize_wn3_params, _map_leads

class ReleaseDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def provider(self, executor=None):
        return WeatherNext3Provider(project='test-project',dataset='linked',executor=executor or FakeExecutor(),cache_dir=self.root)
    def test_no_nearest_lead_substitution(self):
        with self.assertRaises(WeatherNext3Error): _row_for_hour([{'forecast_hour':23}],24)
        with self.assertRaises(WeatherNext3Error): _row_for_hour([{'forecast_hour':24}]*2,24)
    def test_point_gap_is_not_cached(self):
        class Gap(FakeExecutor):
            def query(self,sql,parameters):
                rows=super().query(sql,parameters)
                return rows[:-1] if 'AS grid_lon' in sql else rows
        with self.assertRaises(WeatherNext3Error): self.provider(Gap()).point_series('test',55.75,37.62,24)
        self.assertFalse(list(self.root.glob('point_surface*.json')))
    def test_map_duplicates_are_rejected(self):
        class Duplicate(FakeExecutor):
            def query(self,sql,parameters):
                rows=super().query(sql,parameters)
                return rows+rows[:1] if 'AS longitude' in sql else rows
        with self.assertRaises(WeatherNext3Error): self.provider(Duplicate()).map_frames('t',55.75,37.62,[1])
        self.assertFalse(list(self.root.glob('map_*.json')))
    def test_map_grid_missing_in_one_frame_is_rejected(self):
        class Grid(FakeExecutor):
            def query(self,sql,parameters):
                rows=super().query(sql,parameters)
                if 'AS longitude' in sql:
                    extra={**rows[0], 'forecast_hour':2,'forecast_time':rows[0]['forecast_time']+timedelta(hours=1)}
                    return rows+[extra]
                return rows
        with self.assertRaises(WeatherNext3Error): self.provider(Grid()).map_frames('t',55.75,37.62,[1,2])
    def test_station_missing_stat_falls_back_consistently(self):
        series=self.provider().point_series('t',55.75,37.62,2)
        series.rows[-1]['station_temperature_p10']=None
        rows=surface_rows(series)
        self.assertEqual(len({row['temperature_source'] for row in rows}),1)
        self.assertAlmostEqual(rows[0]['temperature_mean_c'],2.)
        self.assertAlmostEqual(rows[0]['temperature_p10_c'],0.)
    def test_cache_serializes_concurrent_writers_and_invalidates_corruption(self):
        path=self.root/'cache.json'; count=0
        def build():
            nonlocal count
            count+=1; time.sleep(.02); return [{'value':3}]
        def validate(rows):
            if rows != [{'value':3}]: raise ValueError('corrupt')
        with ThreadPoolExecutor(6) as pool:
            results=list(pool.map(lambda _:cached_json(path,60,build,validate),range(6)))
        self.assertEqual(count,1); self.assertEqual(len(results),6)
        path.write_text('[{"broken": 1}]')
        self.assertEqual(cached_json(path,60,build,validate),[{'value':3}]); self.assertEqual(count,2)
    def test_bigquery_budget_default_and_timeout_cancel(self):
        executor=GoogleBigQueryExecutor(billing_project='test-project')
        self.assertEqual(executor.maximum_bytes_billed,1_000_000_000)
        from unittest.mock import MagicMock
        job=MagicMock(); job.result.side_effect=TimeoutError('timeout')
        client=MagicMock(); client.query.return_value=job
        executor._client=client
        with self.assertRaises(WeatherNext3Error): executor.query('SELECT @x',{'x':1})
        job.cancel.assert_called_once()
        config=client.query.call_args.kwargs['job_config']
        self.assertEqual(config.maximum_bytes_billed,1_000_000_000)
    def test_cancellation_stops_next_query(self):
        flag=threading.Event(); flag.set(); executor=FakeExecutor()
        with self.assertRaises(Cancelled), cancellation(flag): self.provider(executor).point_series('t',55.75,37.62,1)
        self.assertFalse(executor.calls)
    def test_wind_calm_and_finite_probability_denominator(self):
        np.testing.assert_allclose(wind_from(np.array([0.,0.]),np.array([-5.,0.])),[0.,np.nan],equal_nan=True)
        result, counts=exceedance_probability(np.array([[2.,np.nan],[0.,np.nan],[np.nan,np.nan]]),1)
        np.testing.assert_allclose(result,[50.,np.nan],equal_nan=True)
    def test_long_matrix_step_matches_actual_leads(self):
        p=normalize_wn3_params({'kind':'windgram','from':1,'to':360,'step':1})
        leads=_map_leads(p); self.assertLessEqual(len(leads),32); self.assertEqual(p['step'],leads[1]-leads[0])

class ZarrReleaseTests(unittest.TestCase):
    def test_python310_is_rejected_before_any_cloud_request(self):
        from weathernext3_status import status
        with patch('weathernext3_zarr.sys.version_info', (3, 10, 0)):
            with self.assertRaisesRegex(WeatherNext3Error, 'Python 3.11'):
                WeatherNext3ZarrProvider.from_env()
            with self.assertRaisesRegex(WeatherNext3Error, 'Python 3.11'):
                WeatherNext3ZarrProvider(billing_project='test').runs()
            self.assertFalse(status()['upper_runtime_supported'])
    def test_pairwise_exact_hour_selection(self):
        ds=upper_dataset(); array=ds.temperature.sel(level=1000).isel(init_time=0,sample=0,lat_0p25=0,lon_0p25=0)
        selected=select_hours(array,[1,6,7,24]); self.assertEqual(selected.shape,(4,))
        np.testing.assert_array_equal(selected.lead_time.values+selected.lead_subtime.values,np.array([1,6,7,24],dtype='timedelta64[h]'))
        with self.assertRaises(WeatherNext3Error): select_hours(array,[25])
    def test_both_pressure_schemas_and_mean_scalar_wind(self):
        for flattened in (False,True):
            profile=upper_provider(upper_dataset(flattened=flattened)).profiles(POINT.lat,POINT.lon,[1])[0]
            self.assertEqual(len(profile.dataframe),13); self.assertAlmostEqual(profile.grid_lat,55.75)
            row=profile.dataframe.iloc[0]
            self.assertAlmostEqual(row.temperature_c,18.,places=5)
            self.assertAlmostEqual(row.dewpoint_c,10.,places=4)
            self.assertAlmostEqual(row.wind_speed_ms,5.)
            self.assertAlmostEqual(np.hypot(row.u_wind_ms,row.v_wind_ms),2.5)
            self.assertIn('4/64',profile.model_label)
            self.assertNotIn('gfs_grid_point',profile.to_payload())
    def test_member_selection_and_synoptic_fallback(self):
        runs=[ZarrRun(INIT+timedelta(hours=6),'new'),ZarrRun(INIT,'old')]
        def opener(uri):
            ds=upper_dataset()
            if uri=='new':
                ds=ds.assign_coords(init_time=[np.datetime64('2026-09-08T06:00')]).isel(lead_time=slice(0,1))
            return ds
        provider=WeatherNext3ZarrProvider(candidates=lambda:runs,opener=opener)
        profile=provider.profiles(POINT.lat,POINT.lon,[24],member='1')[0]
        self.assertEqual(profile.run.cycle,'00'); self.assertLess(profile.dataframe.iloc[0].u_wind_ms,0)
    def test_circular_longitude_and_descending_latitude(self):
        arr=xr.DataArray(np.ones((3,3)),dims=['lat_0p25','lon_0p25'],coords={'lat_0p25':[1,.75,.5],'lon_0p25':[0,.25,359.75]})
        _,lat,lon=select_point(arr,.74,-.24)
        self.assertEqual((lat,lon),(.75,-.25))
    def test_chunk_subset_and_read_limits(self):
        for kwargs in ({'max_chunk_bytes':1},{'max_subset_bytes':1},{'max_read_bytes':1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(WeatherNext3Error):
                upper_provider(**kwargs).profiles(POINT.lat,POINT.lon,[1,24])
    def test_corrupt_time_and_incomplete_ensemble_rejected(self):
        ds=upper_dataset(); ds['specific_humidity'][dict(sample=0)]=np.nan
        with self.assertRaises(WeatherNext3Error): upper_provider(ds).profiles(POINT.lat,POINT.lon,[1])
        ds=upper_dataset().assign_coords(lead_subtime=np.arange(6))
        with self.assertRaises(WeatherNext3Error): upper_provider(ds).profiles(POINT.lat,POINT.lon,[1])
    @unittest.skipIf(sys.version_info < (3, 11), 'GCS/Zarr 3 requires Python 3.11+; surface tests remain active')
    def test_real_zarr_roundtrip_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.zarr'; upper_dataset().to_zarr(path,zarr_format=2)
            provider=WeatherNext3ZarrProvider(candidates=lambda:[ZarrRun(INIT,'fixture')],
                opener=lambda _:xr.open_zarr(path,chunks=None),cache_dir=Path(tmp)/'cache')
            a=provider.profiles(POINT.lat,POINT.lon,[1,24]); b=provider.profiles(POINT.lat,POINT.lon,[1,24])
            self.assertEqual(a[1].valid_time_utc,INIT+timedelta(hours=24))
            self.assertTrue(a[0].dataframe.equals(b[0].dataframe))
            self.assertEqual(len(list((Path(tmp)/'cache').glob('profile_*.json'))),1)
