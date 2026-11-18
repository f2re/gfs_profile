from __future__ import annotations

from wn3_test_support import enable_for_module as setUpModule

import unittest
from datetime import datetime, timezone
import numpy as np

from weathernext3_meteogram import fetch_weathernext3_meteogram
from weathernext3_provider import WeatherNext3PointSeries, WeatherNext3Run


class FakeProvider:
    def point_series(self, point_label, lat, lon, lead_to):
        row = {"forecast_time": datetime(2026,9,7,1,tzinfo=timezone.utc), "forecast_hour":1, "temperature_mean":280.15, "dewpoint_mean":275.15, "station_temperature_mean":281.15, "station_dewpoint_mean":277.15, "pressure_mean":101000.0, "cloud_total_mean":0.75, "cloud_low_mean":0.2, "cloud_mid_mean":0.3, "cloud_high_mean":0.4, "wind_u_mean":0.0, "wind_v_mean":-5.0, "precip_native_mean":0.001, "precip_imerg_mean":0.002, "precip_experimental_mean":0.003, "solar_mean":100000.0}
        for prefix, base in (("temperature",280.15),("dewpoint",275.15),("station_temperature",281.15),("station_dewpoint",277.15)):
            for suffix, shift in (("p10",-2),("p25",-1),("p50",0),("p75",1),("p90",2),("mean",0)): row[f"{prefix}_{suffix}"]=base+shift
        for prefix, base in (("wind_speed",5.0),("precip_native",0.001)):
            for suffix in ("p10","p25","p50","p75","p90","mean"): row[f"{prefix}_{suffix}"]=base
        return WeatherNext3PointSeries(WeatherNext3Run(datetime(2026,9,7,0,tzinfo=timezone.utc),360), point_label, lat, lon, 55.8,37.6,55.75,37.60,[row])


class MeteogramAdapterTests(unittest.TestCase):
    def test_station_head_and_units_are_used(self):
        series=fetch_weathernext3_meteogram("Москва",55.75,37.62,1,provider=FakeProvider())
        self.assertAlmostEqual(series.values("temperature_2m")[0],8.0,places=4)
        self.assertAlmostEqual(series.values("dew_point_2m")[0],4.0,places=4)
        self.assertAlmostEqual(series.values("pressure_msl")[0],1010.0,places=4)
        self.assertAlmostEqual(series.values("precipitation")[0],1.0,places=4)
        self.assertAlmostEqual(series.values("precipitation_imerg")[0],2.0,places=4)
        self.assertAlmostEqual(series.values("cloud_cover")[0],75.0,places=4)
        self.assertAlmostEqual(series.values("wind_direction_10m")[0],0.0,places=4)
        self.assertTrue(0.0 <= series.values("relative_humidity_2m")[0] <= 100.0)
        self.assertIsNone(series.member_count); self.assertTrue(series.ensemble_statistics_only); self.assertEqual(series.init_time_utc.hour,0)
        self.assertTrue(np.isnan(series.values("wind_gusts_10m")[0]))

if __name__ == "__main__": unittest.main()
