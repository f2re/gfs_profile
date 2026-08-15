from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np


class MeteogramError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MeteogramSource:
    source_id: str
    label: str
    model: str
    provider: str
    endpoint: str
    upstream_id: str
    horizon_days: int
    ensemble: bool = False
    expected_members: int | None = None
    resolution: str | None = None


SOURCES = (
    # The Telegram meteogram needs a complete, continuous surface series.
    # Open-Meteo's /v1/gfs route defaults to gfs_seamless; using the legacy
    # gfs025 selector has produced incomplete current responses in operation.
    # gfs_seamless joins the currently available NOAA/NCEP GFS grids while
    # keeping one continuous surface forecast. Native /profile and /cloudgram
    # remain direct NOMADS GFS 0.25° products and are unaffected by this choice.
    MeteogramSource(
        "gfs",
        "GFS · NOAA/NCEP",
        "NOAA GFS Global seamless",
        "NOAA/NCEP через Open-Meteo",
        "https://api.open-meteo.com/v1/gfs",
        "gfs_seamless",
        16,
        resolution="0.11°/0.25°",
    ),
    MeteogramSource("ecmwf_ifs", "ECMWF IFS", "ECMWF IFS 0.25° Open Data", "ECMWF через Open-Meteo", "https://api.open-meteo.com/v1/ecmwf", "ecmwf_ifs025", 15, resolution="0.25°"),
    MeteogramSource("ecmwf_aifs", "ECMWF AIFS", "ECMWF AIFS 0.25° Single", "ECMWF через Open-Meteo", "https://api.open-meteo.com/v1/ecmwf", "ecmwf_aifs025_single", 15, resolution="0.25°"),
    MeteogramSource("icon_global", "ICON · DWD", "DWD ICON Global", "DWD через Open-Meteo", "https://api.open-meteo.com/v1/dwd-icon", "dwd_icon_global", 8, resolution="около 11 км"),
    MeteogramSource("gem_gdps", "GEM · ECCC", "ECCC GEM Global (GDPS)", "ECCC через Open-Meteo", "https://api.open-meteo.com/v1/gem", "cmc_gem_gdps", 10, resolution="около 15 км"),
    MeteogramSource("gefs", "GEFS 0.25° · NOAA/NCEP", "NOAA GEFS 0.25°", "NOAA/NCEP через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "ncep_gefs025", 10, True, 31, "0.25°"),
    MeteogramSource("ecmwf_ens", "ECMWF ENS", "ECMWF IFS Ensemble 0.25°", "ECMWF через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "ecmwf_ifs025_ensemble", 15, True, 51, "0.25°"),
    MeteogramSource("aifs_ens", "AIFS ENS", "ECMWF AIFS Ensemble 0.25°", "ECMWF через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "ecmwf_aifs025_ensemble", 15, True, 51, "0.25°"),
    # Native ICON Global EPS horizon is 7.5 days. Offer only complete days.
    MeteogramSource("icon_eps", "ICON-EPS", "DWD ICON Global EPS", "DWD через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "icon_global_eps", 7, True, 40),
    MeteogramSource("geps", "GEPS · ECCC", "ECCC Global Ensemble Prediction System", "ECCC через Open-Meteo", "https://ensemble-api.open-meteo.com/v1/ensemble", "gem_global_ensemble", 16, True, 21),
)
SOURCE_BY_ID = {source.source_id: source for source in SOURCES}
ALIASES = {
    "noaa": "gfs", "gfs025": "gfs", "ecmwf": "ecmwf_ifs", "ifs": "ecmwf_ifs",
    "aifs": "ecmwf_aifs", "icon": "icon_global", "gem": "gem_gdps",
    "ens": "ecmwf_ens", "ecmwf_ensemble": "ecmwf_ens", "gefs025": "gefs",
    "aifs_ensemble": "aifs_ens", "icon-eps": "icon_eps", "gem_ensemble": "geps",
}


@dataclass(slots=True)
class MeteogramSeries:
    source: MeteogramSource
    point_label: str
    requested_lat: float
    requested_lon: float
    grid_lat: float | None
    grid_lon: float | None
    timezone: str
    times: list[datetime]
    fields: dict[str, np.ndarray]
    stats: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    retrieved_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    member_count: int | None = None
    expected_member_count: int | None = None
    warnings: list[str] = field(default_factory=list)

    def values(self, name: str) -> np.ndarray:
        return self.fields.get(name, np.full(len(self.times), np.nan, dtype=float))

    def statistic(self, name: str, stat: str) -> np.ndarray:
        return self.stats.get(name, {}).get(stat, np.full(len(self.times), np.nan, dtype=float))


def source_for_id(value: str) -> MeteogramSource:
    key = str(value or "gfs").strip().lower().replace("-", "_")
    key = ALIASES.get(key, key)
    try:
        return SOURCE_BY_ID[key]
    except KeyError as exc:
        raise MeteogramError(f"Неизвестная модель: {value}") from exc


def sources_by_kind(ensemble: bool) -> tuple[MeteogramSource, ...]:
    return tuple(source for source in SOURCES if source.ensemble is ensemble)


def available_periods(source: MeteogramSource) -> tuple[int, ...]:
    values = [value for value in (3, 5, 8, 10, 15, 16) if value <= source.horizon_days]
    if source.horizon_days not in values:
        values.append(source.horizon_days)
    return tuple(sorted(set(values)))


def validate_days(source: MeteogramSource, days: int) -> int:
    if days < 1 or days > source.horizon_days:
        raise MeteogramError(f"Для {source.label} доступно 1–{source.horizon_days} суток")
    return days


def grid_distance_km(series: MeteogramSeries) -> float | None:
    if series.grid_lat is None or series.grid_lon is None:
        return None
    lat1, lat2 = math.radians(series.requested_lat), math.radians(series.grid_lat)
    dlat = lat2 - lat1
    dlon = math.radians(series.grid_lon - series.requested_lon)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))
