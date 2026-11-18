from __future__ import annotations

from wn3_test_support import enable_for_module as setUpModule

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weathernext3_provider import WeatherNext3Error, WeatherNext3Provider

RUN = datetime(2026, 9, 7, 0, tzinfo=timezone.utc)


class FakeExecutor:
    def __init__(self): self.calls = []
    def query(self, sql, parameters):
        rows = self._query(sql, parameters)
        if "lead_to" in parameters:
            return [{**row, "forecast_hour": hour, "forecast_time": RUN + timedelta(hours=hour)} for hour in range(1, parameters["lead_to"]+1) for row in rows]
        return rows

    def _query(self, sql, parameters):
        self.calls.append((sql, dict(parameters)))
        if "MAX(f.hours) AS max_hour" in sql:
            return [{"init_time": RUN, "max_hour": 360}]
        if "station_head_temperature_2m_mean" in sql:
            return [{"forecast_time": RUN + timedelta(hours=1), "forecast_hour": 1, "station_grid_lon": 37.60, "station_grid_lat": 55.75, "station_temperature_mean": 274.15, "station_temperature_p10": 273.15, "station_temperature_p25": 273.65, "station_temperature_p50": 274.0, "station_temperature_p75": 274.5, "station_temperature_p90": 275.15, "station_dewpoint_mean": 272.15, "station_dewpoint_p10": 271.15, "station_dewpoint_p25": 271.65, "station_dewpoint_p50": 272.0, "station_dewpoint_p75": 272.5, "station_dewpoint_p90": 273.15}]
        if "AS grid_lon" in sql:
            row = {"forecast_time": RUN + timedelta(hours=1), "forecast_hour": 1, "grid_lon": 37.6, "grid_lat": 55.8, "pressure_mean": 101325.0, "cloud_total_mean": 0.8, "cloud_total_p10": 0.5, "cloud_total_p90": 0.95, "cloud_low_mean": 0.4, "cloud_mid_mean": 0.2, "cloud_high_mean": 0.6, "wind_u_mean": 3.0, "wind_v_mean": 4.0, "precip_imerg_mean": 0.0015, "precip_experimental_mean": 0.002, "solar_mean": 360000.0}
            for suffix, value in (("mean", 275.15), ("p10", 273.15), ("p25", 274.15), ("p50", 275.15), ("p75", 276.15), ("p90", 277.15)):
                row[f"temperature_{suffix}"] = value; row[f"dewpoint_{suffix}"] = value - 2.0; row[f"wind_speed_{suffix}"] = 5.0; row[f"precip_native_{suffix}"] = 0.001
            return [row]
        if "AS longitude" in sql:
            return [{"forecast_time": RUN + timedelta(hours=1), "forecast_hour": 1, "longitude": 37.60, "latitude": 55.75, "cloud_total": 0.75, "precip": 0.0012}, {"forecast_time": RUN + timedelta(hours=1), "forecast_hour": 1, "longitude": 37.70, "latitude": 55.75, "cloud_total": 0.50, "precip": 0.0002}]
        raise AssertionError(sql)


class ProviderTests(unittest.TestCase):
    def make_provider(self, executor):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        return WeatherNext3Provider(project="weather-project", dataset="linked_dataset", executor=executor, cache_dir=Path(tmp.name), cache_ttl_seconds=60)

    def test_latest_run_is_discovered_from_published_data(self):
        executor = FakeExecutor(); provider = self.make_provider(executor)
        run = provider.latest_run(55.75, 37.62, 120)
        self.assertEqual(run.init_time_utc, RUN); self.assertEqual(run.horizon_hours, 360)
        sql, params = executor.calls[0]
        self.assertIn("t.init_time >=", sql); self.assertIn("ST_INTERSECTS", sql); self.assertEqual(params["required_hour"], 120)

    def test_point_series_uses_partition_and_station_head(self):
        executor = FakeExecutor(); provider = self.make_provider(executor)
        point = provider.point_series("Москва", 55.75, 37.62, 48)
        self.assertEqual(point.station_grid_lat, 55.75); self.assertEqual(point.rows[0]["station_temperature_mean"], 274.15)
        point_sql = [sql for sql, _ in executor.calls if "AS grid_lon" in sql][0]
        station_sql = [sql for sql, _ in executor.calls if "station_head_temperature_2m_mean" in sql][0]
        self.assertIn("init_time = @init_time", point_sql); self.assertIn("init_time = @init_time", station_sql); self.assertNotIn("SELECT *", point_sql.upper())

    def test_map_converts_fraction_and_metres(self):
        executor = FakeExecutor(); provider = self.make_provider(executor)
        frames = provider.map_frames("Москва", 55.75, 37.62, [1], kind="combo", radius_km=150)
        self.assertAlmostEqual(frames[0].rows[0]["cloud_total"], 75.0); self.assertAlmostEqual(frames[0].rows[0]["precip"], 1.2)
        map_sql = [sql for sql, _ in executor.calls if "AS longitude" in sql][0]
        self.assertIn("t.init_time = @init_time", map_sql); self.assertIn("ST_DWITHIN", map_sql)

    def test_lead_zero_is_rejected_for_maps(self):
        with self.assertRaises(WeatherNext3Error): self.make_provider(FakeExecutor()).map_frames("Москва", 55.75, 37.62, [0])

    def test_identifier_is_not_interpolated_unsafely(self):
        with self.assertRaises(WeatherNext3Error): WeatherNext3Provider(project="x`; DROP TABLE y;--", dataset="safe", executor=FakeExecutor())


if __name__ == "__main__": unittest.main()
