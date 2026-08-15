from __future__ import annotations

"""Local dependency/layout smoke for DOCX/PDF meteogram reports."""

import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from meteogram_report import write_meteogram_report


@dataclass
class _Source:
    source_id: str = "gefs"
    label: str = "GEFS 0.25° · NOAA/NCEP"
    model: str = "NOAA GEFS 0.25°"
    provider: str = "NOAA/NCEP через Open-Meteo"
    resolution: str = "0.25°"
    ensemble: bool = True


@dataclass
class _Series:
    times: list[datetime]
    fields: dict[str, np.ndarray]
    stats: dict[str, dict[str, np.ndarray]]
    daily_dates: list
    daily_stats: dict[str, dict[str, np.ndarray]]
    source: _Source = field(default_factory=_Source)
    point_label: str = "Проверка отчёта"
    requested_lat: float = 55.75
    requested_lon: float = 37.62
    grid_lat: float = 55.75
    grid_lon: float = 37.5
    timezone: str = "Europe/Moscow"
    retrieved_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    member_count: int = 31
    expected_member_count: int = 31
    warnings: list[str] = field(default_factory=list)
    sampling_mode: str = "raw_model_grid"

    def values(self, name: str) -> np.ndarray:
        return self.fields.get(name, np.full(len(self.times), np.nan))

    def statistic(self, name: str, stat: str) -> np.ndarray:
        return self.stats.get(name, {}).get(stat, np.full(len(self.times), np.nan))

    def daily_statistic(self, name: str, stat: str) -> np.ndarray:
        return self.daily_stats.get(name, {}).get(stat, np.full(len(self.daily_dates), np.nan))


def _series() -> _Series:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = [start + timedelta(hours=3 * index) for index in range(25)]
    count = len(times)
    temperature = np.linspace(-8, 4, count)
    precipitation = np.zeros(count)
    precipitation[8:11] = (0.2, 0.6, 0.2)
    fields = {
        "temperature_2m": temperature,
        "dew_point_2m": temperature - 3,
        "relative_humidity_2m": np.linspace(70, 94, count),
        "precipitation": precipitation,
        "wind_speed_10m": np.full(count, 5.0),
        "wind_gusts_10m": np.full(count, 9.0),
        "wind_direction_10m": np.full(count, 250.0),
        "pressure_msl": np.linspace(1018, 1008, count),
        "cloud_cover": np.linspace(30, 95, count),
        "weather_code": np.where(precipitation > 0, 71, 3),
        "ensemble_member_count": np.full(count, 31.0),
        "precipitation_accumulation_hours": np.full(count, 3.0),
        "precipitation_probability_0p1": np.where(precipitation > 0, 65.0, 10.0),
        "precipitation_probability_1": np.where(precipitation > 0, 20.0, 0.0),
        "precipitation_probability_5": np.zeros(count),
    }
    stats = {
        "temperature_2m": {"q10": temperature - 2, "q90": temperature + 2},
        "wind_gusts_10m": {"q90": np.full(count, 12.0)},
    }
    dates = list(dict.fromkeys(item.date() for item in times))
    daily = np.array([0.8, 0.2, 0.0])[: len(dates)]
    daily_stats = {
        "precipitation": {
            "q10": np.maximum(0, daily - 0.2),
            "q50": daily,
            "q90": daily + 1.0,
            "coverage_hours": np.full(len(dates), 24.0),
            "complete_day": np.ones(len(dates)),
        }
    }
    return _Series(times, fields, stats, dates, daily_stats)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gfs_report_smoke_") as tmp:
        directory = Path(tmp)
        chart = directory / "meteogram.png"
        image = Image.new("RGB", (1400, 760), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 90, 1320, 680), outline="#52748a", width=4)
        draw.text((110, 120), "Meteogram report smoke", fill="#17324d")
        image.save(chart)

        series = _series()
        docx = write_meteogram_report(series, chart, "docx", output_dir=directory)
        if not docx.path.is_file() or docx.path.stat().st_size < 10_000:
            raise RuntimeError("DOCX smoke failed")
        pdf_note = "PDF skipped: soffice not installed"
        if shutil.which("soffice") or shutil.which("libreoffice"):
            pdf = write_meteogram_report(
                series,
                chart,
                "pdf",
                output_dir=directory,
                pdf_fallback_to_docx=False,
            )
            if not pdf.path.is_file() or pdf.path.stat().st_size < 10_000:
                raise RuntimeError("PDF smoke failed")
            pdf_note = "PDF OK"
        print(f"Meteogram report smoke OK: DOCX OK; {pdf_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
