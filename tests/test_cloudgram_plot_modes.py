from __future__ import annotations

import unittest
from datetime import timedelta

from cloudgram_plot import write_cloudgram_png
from cloudgram_product import CloudgramCell, CloudgramData
from gfs_core import GfsRun


class CloudgramPlotModeTests(unittest.TestCase):
    def _data(self) -> CloudgramData:
        run = GfsRun("20260701", "06")
        cells = []
        for lead, cloud, hazard in ((0, 10.0, 0), (3, 65.0, 2), (6, 95.0, 4)):
            cells.append(
                CloudgramCell(
                    lead_hour=lead,
                    valid_time_utc=run.run_datetime_utc + timedelta(hours=lead),
                    high_cloud_pct=cloud,
                    mid_cloud_pct=cloud,
                    low_cloud_pct=cloud,
                    total_cloud_pct=cloud,
                    ceiling_m=1500.0,
                    precip_mm=0.5 if lead else 0.0,
                    precip_rate_mmh=0.0,
                    conv_precip_mm=0.0,
                    precip_type="R" if lead else "—",
                    cape_jkg=100.0,
                    cin_jkg=-50.0,
                    cb_score=2 if hazard else 0,
                    visibility_km=10.0,
                    phenomena="TSRA" if hazard >= 4 else ("RA" if lead else "—"),
                    hazard_score=hazard,
                    hazard_text="test",
                )
            )
        return CloudgramData(run, 45.0, 39.0, 45.0, 39.0, [0, 3, 6], cells)

    def test_write_pro_and_simple_png(self) -> None:
        for mode in ("pro", "simple"):
            path = write_cloudgram_png(self._data(), mode=mode)
            try:
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 1000)
            finally:
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
