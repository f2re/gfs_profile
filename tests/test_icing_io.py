import hashlib
import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import numpy as np
import xarray as xr

from gfs_core import GfsProfileError, GfsRun
from icing_config import (DEFAULT_ICING_BBOX, ICING_LEVELS_HPA, IcingRequest, icing_repeat_command,
                          parse_icing_input, validate_icing_request)
from icing_io import (IcingCancelled, _download, _verify_manifest, icing_subset_url,
                      load_icing_input, missing_index_fields, raw_cache_key,
                      read_icing_grib, select_icing_run, validate_grib_file)

RUN = GfsRun("20261117", "06")


def index_text(run=RUN):
    rows = [(v, f"{p} mb") for p in ICING_LEVELS_HPA for v in
            ("TMP", "SPFH", "HGT", "CLWMR", "TCDC", "ICMR", "RWMR", "VVEL")]
    rows += [("PRES", "surface"), ("HGT", "surface")]
    return "\n".join(f"{i}:0:d={run.date}{run.cycle}:{v}:{level}:24 hour fcst:" for i, (v, level) in enumerate(rows, 1))


def framed_bytes(payload=b"test"):
    return b"GRIB\0\0\0\2" + (20+len(payload)).to_bytes(8, "big") + payload + b"7777"


def dataset(surface=False):
    coords = {"latitude": [34.25, 34.0], "longitude": [345., 345.25],
              "time": np.datetime64("2026-11-17T06:00"), "step": np.timedelta64(24, "h"),
              "valid_time": np.datetime64("2026-11-18T06:00")}
    if surface:
        dims, shape = ("latitude", "longitude"), (2, 2)
        variables = {"sp": (100000., "Pa"), "orog": (0., "m")}
    else:
        coords["isobaricInhPa"] = list(ICING_LEVELS_HPA)
        dims, shape = ("isobaricInhPa", "latitude", "longitude"), (17, 2, 2)
        variables = {"t": (270., "K"), "q": (.002, "kg kg**-1"),
                     "gh": (1000., "gpm"), "clwmr": (.0001, "kg kg**-1"),
                     "tcc": (60., "%"), "icmr": (.00002, "kg kg**-1"),
                     "rwmr": (.0000005, "kg kg**-1"), "w": (-.12, "Pa s**-1")}
    return xr.Dataset({k: (dims, np.full(shape, v),
                          {"units": u, "GRIB_typeOfLevel": "surface" if surface else "isobaricInhPa",
                           "GRIB_stepType": "instant"}) for k, (v, u) in variables.items()}, coords=coords)


class IcingConfigTests(unittest.TestCase):
    def test_repeat_command_round_trip_preserves_explicit_cycle_and_bbox(self):
        r = IcingRequest("slw_level", 384, 700, 100, (-12.125, 98.75, 35.0, 79.5), RUN)
        self.assertEqual(parse_icing_input(icing_repeat_command(r)), r)

    def test_environment_default_and_explicit_region(self):
        with patch.dict("os.environ", {"ICING_BBOX": "0,90,35,75"}):
            self.assertEqual(parse_icing_input("").bbox, (0, 90, 35, 75))
            self.assertEqual(parse_icing_input("bbox=10,20,40,50").bbox, (10, 20, 40, 50))

    def test_focused_default_covers_requested_landmarks(self):
        with patch.dict("os.environ", {"ICING_BBOX": ""}):
            request = parse_icing_input("")
        self.assertEqual(request.bbox, (12, 85, 40.5, 70.5))
        west, east, south, north = request.bbox
        for lat, lon in ((52.52, 13.405), (55.03, 82.92), (68.97, 33.08), (41.29, 36.33)):
            with self.subTest(lat=lat, lon=lon):
                self.assertTrue(south <= lat <= north and west <= lon <= east)

    def test_old_environment_template_adopts_new_default_without_changing_explicit_request(self):
        for old in ("-15,100,34,80", " -15.0, 100.0, 34.00, 80 "):
            with self.subTest(old=old), patch.dict("os.environ", {"ICING_BBOX": old}):
                self.assertEqual(parse_icing_input("").bbox, DEFAULT_ICING_BBOX)
                self.assertEqual(parse_icing_input("bbox=-15,100,34,80").bbox, (-15, 100, 34, 80))
                self.assertEqual(os.environ["ICING_BBOX"], old)

    def test_invalid_inputs_are_rejected_before_work(self):
        for command in ("+121", "+24.5", "level=875", "contours=25", "contours=100dam", "product=intensity",
                        "run=20260230/06", "bbox=nan,100,34,80", "bbox=100,-15,34,80",
                        "bbox=-180,180,0,85", "+24 +48", "unknown=1"):
            with self.subTest(command=command), self.assertRaises(GfsProfileError):
                parse_icing_input(command)
        with self.assertRaises(GfsProfileError):
            validate_icing_request(replace(IcingRequest(), lead=True))

    def test_decametre_commands_preserve_legacy_contour_spacing(self):
        for value, metres in (("10dam", 100), ("50дам", 500), ("10", 100),
                              ("50", 500), ("100", 100), ("500", 500)):
            with self.subTest(value=value):
                self.assertEqual(parse_icing_input(f"contours={value}").contour_step, metres)
        self.assertIn("contours=50dam", icing_repeat_command(IcingRequest()))

    def test_marked_point_round_trip_and_region_validation(self):
        request = parse_icing_input("/icing product=slw_max +24 point=55.75583,37.61731 run=20261117/06")
        self.assertEqual(request.point, (55.7558, 37.6173))
        self.assertEqual(parse_icing_input(icing_repeat_command(request)), request)
        eastern = parse_icing_input("bbox=130,140,50,60 point=55.1234,137.1234")
        self.assertEqual(parse_icing_input(icing_repeat_command(eastern)), eastern)
        for value in ("nan,37", "inf,37", "55", "55,37,1", "91,37", "55,181", "55,140"):
            with self.subTest(value=value), self.assertRaises(GfsProfileError):
                parse_icing_input(f"point={value}")
        self.assertEqual(parse_icing_input("bbox=37,38,55,56 point=55,38").point, (55, 38))


class IcingInputTests(unittest.TestCase):
    def test_index_checks_fields_levels_and_cycle(self):
        self.assertEqual(missing_index_fields(index_text(), RUN), [])
        bad = index_text().replace(":CLWMR:850 mb:", ":ICMR:850 mb:")
        self.assertEqual(missing_index_fields(bad, RUN), ["CLWMR 850 mb"])
        self.assertTrue(missing_index_fields(index_text(), GfsRun("20261117", "12")))

    def test_index_requires_cloud_and_phase_fields_at_each_pressure_level(self):
        for variable in ("TCDC", "ICMR", "RWMR", "VVEL"):
            for pressure in (1000, 850, 300):
                with self.subTest(variable=variable, pressure=pressure):
                    bad = index_text().replace(f":{variable}:{pressure} mb:", f":{variable}:entire atmosphere:")
                    self.assertEqual(missing_index_fields(bad, RUN), [f"{variable} {pressure} mb"])

    def test_incomplete_new_cycle_falls_back_for_the_exact_lead(self):
        new, old = GfsRun("20261117", "12"), RUN
        selector = Mock(side_effect=[new, old])
        with patch("icing_io.now_utc", return_value=datetime(2026, 11, 17, 15, tzinfo=timezone.utc)), \
             patch("icing_io.read_icing_index", side_effect=[GfsProfileError("missing CLWMR"), index_text()]):
            actual, _ = select_icing_run(IcingRequest(lead=384), run_selector=selector)
        self.assertEqual(actual, old)
        self.assertEqual([call.args[0] for call in selector.call_args_list], [384, 384])
        self.assertLess(selector.call_args_list[1].kwargs["reference_time"], new.run_datetime_utc)

    def test_explicit_cycle_is_never_silently_replaced(self):
        selector = Mock()
        with patch("icing_io.read_icing_index", side_effect=GfsProfileError("not published")), self.assertRaises(GfsProfileError):
            select_icing_run(IcingRequest(run=RUN), run_selector=selector)
        selector.assert_not_called()

    def test_region_and_fields_are_present_in_filter_request_and_cache_key(self):
        bbox = (-15, 100, 34, 80)
        query = parse_qs(urlparse(icing_subset_url(RUN, 384, bbox)).query, keep_blank_values=True)
        self.assertEqual(query["file"], ["gfs.t06z.pgrb2.0p25.f384"])
        self.assertEqual(query["leftlon"], ["-15.000"])
        for variable in ("TMP", "SPFH", "HGT", "CLWMR", "TCDC", "ICMR", "RWMR", "VVEL"):
            self.assertEqual(query[f"var_{variable}"], ["on"])
        self.assertIn("lev_300_mb", query)
        self.assertNotIn("all_lev", query)
        self.assertNotIn("lev_entire_atmosphere", query)
        self.assertTrue(raw_cache_key(RUN, 24, bbox).startswith("icing_raw_v2_"))
        self.assertNotEqual(raw_cache_key(RUN, 24, bbox), raw_cache_key(RUN, 384, bbox))
        self.assertNotEqual(raw_cache_key(RUN, 24, bbox), raw_cache_key(RUN, 24, (-15, 99, 34, 80)))

    def test_truncated_messages_html_and_trailing_bytes_are_not_grib(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "sample.grib2"
            p.write_bytes(framed_bytes() + framed_bytes(b"second"))
            validate_grib_file(p)
            for payload in (b"<html>error</html>", framed_bytes()[:-1], framed_bytes()+b"error", b""):
                p.write_bytes(payload)
                with self.assertRaises(GfsProfileError):
                    validate_grib_file(p)

    def test_manifest_detects_packed_data_corruption_even_with_valid_framing(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "sample.grib2"
            p.write_bytes(framed_bytes())
            manifest = Path(directory) / "source.json"
            manifest.write_text(json.dumps({"files": [{"name": p.name, "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}]}))
            _verify_manifest(manifest, (p, p))
            p.write_bytes(framed_bytes(b"xxxx"))
            validate_grib_file(p)
            with self.assertRaises(GfsProfileError):
                _verify_manifest(manifest, (p, p))

    def test_cfgrib_strict_filters_coordinates_time_and_units(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"sample.grib2"
            p.write_bytes(framed_bytes())
            with patch("xarray.open_dataset", side_effect=[dataset(), dataset(True)]) as opener:
                result = read_icing_grib(p, p, RUN, 24, (-15, -14.75, 34, 34.25))
            self.assertEqual(result.lat.shape, (2, 2))
            self.assertEqual(result.lon[0, 0], -15)
            self.assertEqual(opener.call_args_list[0].kwargs["backend_kwargs"]["errors"], "raise")
            self.assertEqual(opener.call_args_list[0].kwargs["backend_kwargs"]["filter_by_keys"], {"typeOfLevel":"isobaricInhPa"})
            for bad in (dataset().assign_coords(valid_time=np.datetime64("2026-11-18T09:00")),
                        dataset().assign_coords(longitude=[100., 100.25])):
                with patch("xarray.open_dataset", return_value=bad), self.assertRaises(GfsProfileError):
                    read_icing_grib(p, p, RUN, 24, (-15, -14.75, 34, 34.25))
            bad = dataset()
            bad["gh"].attrs["units"] = "Pa"
            with patch("xarray.open_dataset", return_value=bad), self.assertRaises(GfsProfileError):
                read_icing_grib(p, p, RUN, 24, (-15, -14.75, 34, 34.25))

    def test_cloud_phase_and_vertical_motion_arrays_preserve_units_and_missing_values(self):
        keys = {"tcc": "cloud_fraction_pct", "icmr": "cloud_ice_kgkg",
                "rwmr": "rain_water_kgkg", "w": "vertical_velocity_pas"}
        atmosphere = dataset()
        for name in keys:
            atmosphere[name].values[0, 0, 0] = np.nan
            atmosphere[name].values[0, 0, 1] = 0
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "sample.grib2"
            p.write_bytes(framed_bytes())
            with patch("xarray.open_dataset", side_effect=[atmosphere, dataset(True)]):
                result = read_icing_grib(p, p, RUN, 24, (-15, -14.75, 34, 34.25))
        for short, key in keys.items():
            self.assertEqual(result.fields[key].shape, (17, 2, 2))
            np.testing.assert_equal(result.fields[key], atmosphere[short].values)
            self.assertTrue(np.isnan(result.fields[key][0, 0, 0]))
            self.assertEqual(result.fields[key][0, 0, 1], 0)
        self.assertEqual(result.fields["cloud_fraction_pct"][1, 0, 0], 60)
        self.assertEqual(result.fields["vertical_velocity_pas"][1, 0, 0], -.12)

    def test_new_fields_are_required_and_wrong_cloud_layer_or_units_are_rejected(self):
        invalid = []
        for short in ("tcc", "icmr", "rwmr", "w"):
            invalid.append((f"missing {short}", dataset().drop_vars(short)))
        for short, units in (("tcc", "1"), ("w", "m s**-1"), ("icmr", "g kg**-1"), ("rwmr", "g kg**-1")):
            bad = dataset()
            bad[short].attrs["units"] = units
            invalid.append((f"units {short}", bad))
        whole_column = dataset()
        whole_column["tcc"] = whole_column["tcc"].isel(isobaricInhPa=0, drop=True)
        whole_column["tcc"].attrs["GRIB_typeOfLevel"] = "atmosphere"
        invalid.append(("whole-column cloud cover", whole_column))
        average = dataset()
        average["tcc"].attrs["GRIB_stepType"] = "avg"
        invalid.append(("averaged cloud cover", average))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "sample.grib2"
            p.write_bytes(framed_bytes())
            for label, bad in invalid:
                with self.subTest(label=label), patch("xarray.open_dataset", return_value=bad), self.assertRaises(GfsProfileError):
                    read_icing_grib(p, p, RUN, 24, (-15, -14.75, 34, 34.25))

    def test_old_four_field_cache_is_not_reused_for_cloud_composite(self):
        bbox = (-15.0, -14.75, 34.0, 34.25)
        request = IcingRequest(run=RUN, lead=24, bbox=bbox)
        with patch("icing_io.RAW_SCHEMA", "icing_raw_v1"), patch("icing_io.ATMOSPHERIC_VARIABLES", ("TMP", "SPFH", "HGT", "CLWMR")):
            old_key = raw_cache_key(RUN, 24, bbox)
        with tempfile.TemporaryDirectory() as directory, patch("icing_io.CACHE_DIR", Path(directory)):
            old_paths = [Path(directory) / f"{old_key}_{kind}.grib2" for kind in ("levels", "surface")]
            for path in old_paths:
                path.write_bytes(framed_bytes())
            with patch("icing_io.read_icing_index", return_value=index_text()), \
                 patch("icing_io._download", side_effect=lambda url, path, **kwargs: path.write_bytes(framed_bytes())) as download, \
                 patch("xarray.open_dataset", side_effect=[dataset(), dataset(True)]):
                result = load_icing_input(request)
            self.assertEqual(download.call_count, 2)
            self.assertTrue(all(path.exists() for path in old_paths))
            self.assertTrue(all(path.name.startswith("icing_raw_v2_") for path in result.paths))
            metadata = json.loads((Path(directory) / f"{raw_cache_key(RUN, 24, bbox)}.json").read_text())
            self.assertEqual(metadata["schema"], "icing_raw_v2")
            self.assertEqual(metadata["atmospheric_variables"], ["TMP", "SPFH", "HGT", "CLWMR", "TCDC", "ICMR", "RWMR", "VVEL"])
            self.assertEqual(metadata["surface_variables"], ["PRES", "HGT"])
            self.assertEqual(metadata["run_date"], RUN.date)
            self.assertEqual(metadata["lead"], 24)

    def test_cancellation_prevents_network_and_output(self):
        cancel = threading.Event()
        cancel.set()
        with patch("icing_io.requests.get") as get, self.assertRaises(IcingCancelled):
            load_icing_input(IcingRequest(), cancel_event=cancel)
        get.assert_not_called()

    def test_transient_network_failure_retries_but_html_never_enters_cache(self):
        import requests
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"download.grib2"
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.headers = {"content-type": "application/octet-stream"}
            response.iter_content.return_value = [framed_bytes()]
            with patch("icing_io.requests.get", side_effect=[requests.Timeout("temporary"), response]) as get, \
                 patch("icing_io.time.sleep"):
                _download("https://example.invalid/subset", path)
            self.assertEqual(get.call_count, 2)
            validate_grib_file(path)
            path.unlink()
            response.headers = {"content-type": "text/html"}
            with patch("icing_io.requests.get", return_value=response), self.assertRaises(GfsProfileError):
                _download("https://example.invalid/subset", path)
            self.assertFalse(path.exists())
            self.assertFalse(list(Path(directory).glob("*.part")))


if __name__ == "__main__":
    unittest.main()
