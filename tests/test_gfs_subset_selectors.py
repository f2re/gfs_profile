from __future__ import annotations

import unittest

import numpy as np
import xarray as xr

from gfs_subset import GribFieldSelector, scalar_from_datasets, select_grib_field


def _field(name: str, value, *, short_name: str, type_of_level: str, step_type: str = "instant", start_step=None, end_step=None, level_coord=None):
    attrs = {
        "GRIB_shortName": short_name,
        "GRIB_typeOfLevel": type_of_level,
        "GRIB_stepType": step_type,
    }
    if start_step is not None:
        attrs["GRIB_startStep"] = start_step
    if end_step is not None:
        attrs["GRIB_endStep"] = end_step
    if level_coord is None:
        array = xr.DataArray(
            np.asarray([[value]], dtype=float),
            dims=("latitude", "longitude"),
            coords={"latitude": [55.0], "longitude": [37.0]},
            attrs=attrs,
        )
    else:
        coord_name, coord_values, values = level_coord
        array = xr.DataArray(
            np.asarray(values, dtype=float).reshape(len(coord_values), 1, 1),
            dims=(coord_name, "latitude", "longitude"),
            coords={coord_name: coord_values, "latitude": [55.0], "longitude": [37.0]},
            attrs=attrs,
        )
    return xr.Dataset({name: array})


class GfsSubsetSelectorTests(unittest.TestCase):
    def test_type_of_level_prevents_total_cloud_from_becoming_convective_cloud(self) -> None:
        total = _field("tcc_total", 95.0, short_name="tcc", type_of_level="atmosphere")
        convective = _field("tcc_conv", 15.0, short_name="tcc", type_of_level="convectiveCloudLayer")
        value = scalar_from_datasets(
            [total, convective],
            ("tcc",),
            type_of_level=("convectiveCloudLayer",),
            step_types=("instant",),
        )
        self.assertEqual(value, 15.0)

    def test_instant_cloud_is_preferred_over_interval_average(self) -> None:
        average = _field("lcc_avg", 80.0, short_name="lcc", type_of_level="lowCloudLayer", step_type="avg", start_step=0, end_step=3)
        instant = _field("lcc_inst", 30.0, short_name="lcc", type_of_level="lowCloudLayer", step_type="instant", end_step=3)
        value = scalar_from_datasets(
            [average, instant],
            ("lcc",),
            type_of_level=("lowCloudLayer",),
            step_types=("instant",),
        )
        self.assertEqual(value, 30.0)

    def test_accumulation_interval_is_selected_explicitly(self) -> None:
        one_hour = _field("tp_1h", 1.0, short_name="tp", type_of_level="surface", step_type="accum", start_step=2, end_step=3)
        three_hour = _field("tp_3h", 3.0, short_name="tp", type_of_level="surface", step_type="accum", start_step=0, end_step=3)
        value = scalar_from_datasets(
            [one_hour, three_hour],
            ("tp",),
            type_of_level=("surface",),
            step_types=("accum",),
            interval_hours=3.0,
        )
        self.assertEqual(value, 3.0)

    def test_cape_uses_18000_pa_pressure_from_ground_layer(self) -> None:
        dataset = _field(
            "cape",
            0.0,
            short_name="cape",
            type_of_level="pressureFromGroundLayer",
            level_coord=("pressureFromGroundLayer", [9000.0, 18000.0, 25500.0], [300.0, 900.0, 1200.0]),
        )
        selected = select_grib_field(
            [dataset],
            GribFieldSelector(
                names=("cape",),
                type_of_level=("pressureFromGroundLayer",),
                level=18000.0,
                step_types=("instant",),
            ),
        )
        self.assertIsNotNone(selected)
        self.assertEqual(float(np.asarray(selected.data_array.values).squeeze()), 900.0)

    def test_wrong_level_or_type_returns_none_instead_of_first_match(self) -> None:
        surface = _field("cape_surface", 500.0, short_name="cape", type_of_level="surface")
        value = scalar_from_datasets(
            [surface],
            ("cape",),
            type_of_level=("pressureFromGroundLayer",),
            level=18000.0,
            step_types=("instant",),
        )
        self.assertIsNone(value)


if __name__ == "__main__":
    unittest.main()
