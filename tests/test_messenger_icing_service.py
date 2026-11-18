import csv
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from gfs_core import GfsProfileError, GfsRun
from icing_config import ICING_LEVELS_HPA, IcingRequest
from icing_io import IcingCancelled, IcingInput
from icing_meteorology import compute_icing_fields
from messenger.icing_service import build_icing_product_result, write_icing_csv
from messenger.profile_service import cleanup_product_result


def input_case():
    pressure = np.array(ICING_LEVELS_HPA)
    shape = (len(pressure), 2, 2)
    temperature = np.broadcast_to(np.linspace(278, 235, len(pressure))[:, None, None], shape).copy()
    raw = {"pressure_hpa": pressure, "temperature_k": temperature,
           "specific_humidity": np.full(shape, .002),
           "height_gpm": np.broadcast_to(np.linspace(100, 9000, len(pressure))[:, None, None], shape).copy(),
           "cloud_water_kgkg": np.full(shape, .0001),
           "surface_pressure_pa": np.full((2, 2), 102000.), "surface_height_m": np.zeros((2, 2))}
    lon, lat = np.meshgrid([37, 37.25], [55.25, 55])
    return IcingInput(lat, lon, raw, GfsRun("20261117", "06"), 24,
                      (Path("levels.grib2"), Path("surface.grib2")))


def renderer(fields, lat, lon, product, **kwargs):
    target = kwargs["output_path"]
    target.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    return target


class IcingServiceTests(unittest.TestCase):
    def test_common_result_records_actual_cycle_and_independent_delivery_files(self):
        case = input_case()
        request = IcingRequest(bbox=(37, 37.25, 55, 55.25))
        events = []
        with tempfile.TemporaryDirectory() as directory, patch("messenger.icing_service.CACHE_DIR", Path(directory)):
            first = build_icing_product_result(request, input_loader=lambda *a, **kw: case,
                                              renderer=renderer, progress_callback=events.append)
            second = build_icing_product_result(request, input_loader=lambda *a, **kw: case, renderer=renderer)
            self.assertEqual(first.metadata["run_date"], "20261117")
            self.assertEqual(first.metadata["run_cycle"], "06")
            self.assertIn("run=20261117/06", first.repeat_command)
            self.assertFalse(first.metadata["meteorological_skill_validated"])
            self.assertEqual([a.kind for a in first.attachments], ["image"])
            self.assertEqual(list((Path(directory)/"icing").glob("*.csv")), [])
            self.assertEqual(first.metadata["units"], "дам")
            self.assertEqual(first.metadata["raw_units"], "гп.м")
            self.assertEqual(first.metadata["contour_step_dam"], 50)
            self.assertIn("18.11.2026 06:00 UTC", first.summary)
            self.assertIn("NOAA/NCEP", first.summary)
            self.assertNotIn("гп.м", first.summary)
            self.assertTrue(all(a.path.exists() for a in first.attachments))
            self.assertTrue(set(a.path for a in first.attachments).isdisjoint(a.path for a in second.attachments))
            cleanup_product_result(first)
            self.assertTrue(all(a.path.exists() for a in second.attachments))
            self.assertEqual(events[-1].stage, "export")
            cleanup_product_result(second)

    def test_cancel_after_render_removes_partial_products(self):
        case, cancel = input_case(), threading.Event()
        def cancel_renderer(*args, **kwargs):
            result = renderer(*args, **kwargs)
            cancel.set()
            return result
        with tempfile.TemporaryDirectory() as directory, patch("messenger.icing_service.CACHE_DIR", Path(directory)):
            with self.assertRaises(IcingCancelled):
                build_icing_product_result(IcingRequest(), input_loader=lambda *a, **k: case,
                                           renderer=cancel_renderer, cancel_event=cancel)
            self.assertEqual(list((Path(directory)/"icing").glob("*")), [])

    def test_selected_point_reaches_map_summary_and_repeat_command(self):
        case = input_case()
        request = IcingRequest(bbox=(37, 37.25, 55, 55.25), point=(55.125, 37.125))
        draw = Mock(side_effect=renderer)
        with tempfile.TemporaryDirectory() as directory, patch("messenger.icing_service.CACHE_DIR", Path(directory)):
            result = build_icing_product_result(request, input_loader=lambda *a, **kw: case, renderer=draw)
            self.assertEqual(draw.call_args.kwargs["point"], request.point)
            self.assertEqual(result.metadata["requested_point"], [55.125, 37.125])
            self.assertIn("Отмеченный пункт: 55.125°, 37.125°", result.summary)
            self.assertIn("point=55.125,37.125", result.repeat_command)
            self.assertEqual([item.kind for item in result.attachments], ["image"])
            cleanup_product_result(result)

    def test_invalid_lead_never_loads_data(self):
        loader = Mock()
        with self.assertRaises(GfsProfileError):
            build_icing_product_result(IcingRequest(lead=121), input_loader=loader)
        loader.assert_not_called()

    def test_incomplete_warm_profile_is_not_reported_as_absence_of_water(self):
        for missing in (False, True):
            case = input_case()
            case.fields["temperature_k"][:] = 278.15
            if missing:
                case.fields["cloud_water_kgkg"][0] = np.nan
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory, \
                    patch("messenger.icing_service.CACHE_DIR", Path(directory)):
                result = build_icing_product_result(IcingRequest(product="slw_max"),
                    input_loader=lambda *a, **kw: case, renderer=renderer)
                if missing:
                    self.assertNotIn("вода не выявлена", result.summary)
                    self.assertIn("неполные данные по высоте", result.summary)
                    self.assertEqual(result.metadata["nodes_with_incomplete_water_profile"], 4)
                else:
                    self.assertIn("вода не выявлена", result.summary)
                cleanup_product_result(result)

    def test_csv_distinguishes_first_layer_boundary_from_column_boundary(self):
        case = input_case()
        fields = compute_icing_fields(**case.fields)
        fields["godske20_top_first_gpm"][:] = 1300
        fields["godske20_top_first_open"][:] = False
        fields["godske20_upper_open"][:] = True
        request = IcingRequest(product="godske_top", run=case.run)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"result.csv"
            write_icing_csv(path, fields, case.lat, case.lon, request)
            with path.open(encoding="utf-8-sig") as stream:
                row = next(csv.DictReader(stream))
        self.assertEqual(row["value"], "1300")
        self.assertEqual(row["column_upper_open"], "1")
        self.assertEqual(row["first_top_open"], "0")
        self.assertEqual(row["unit"], "гп.м")

    def test_agreement_csv_names_categories_and_keeps_unknown_blank(self):
        case = input_case()
        fields = compute_icing_fields(**case.fields)
        # Numerical fixture: category 4 means both diagnoses exist at different heights.
        fields["agreement"] = np.array([[4., np.nan], [0., 3.]])
        fields["agreement_has_coverage"] = np.isfinite(fields["agreement"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"result.csv"
            write_icing_csv(path, fields, case.lat, case.lon, IcingRequest(product="agreement", run=case.run))
            with path.open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["category_label"], "Признаки в разных слоях")
        self.assertEqual(rows[1]["value"], "")
        self.assertEqual(rows[1]["category_label"], "")
        self.assertEqual(rows[2]["value"], "0")


if __name__ == "__main__":
    unittest.main()
