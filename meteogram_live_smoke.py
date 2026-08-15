from __future__ import annotations

"""Live provider-contract smoke for the Telegram meteogram sources."""

import os

import numpy as np

from meteogram_fetch import fetch_meteogram


def _check_source(source_id: str) -> None:
    series = fetch_meteogram(
        source_id,
        "Санкт-Петербург",
        59.9390,
        30.3160,
        1,
    )
    finite_temperature = int(np.isfinite(series.values("temperature_2m")).sum())
    if finite_temperature < 12:
        raise RuntimeError(
            f"{series.source.label}: недостаточно сроков температуры ({finite_temperature})"
        )
    if series.source.ensemble:
        expected = int(series.expected_member_count or 0)
        observed = int(series.member_count or 0)
        if expected and observed != expected:
            raise RuntimeError(
                f"{series.source.label}: контракт членов изменился, получено {observed}/{expected}"
            )
    print(
        f"meteogram smoke ok: {series.source.label}; "
        f"times={len(series.times)}; members={series.member_count or 1}; "
        f"warnings={len(series.warnings)}"
    )
    for warning in series.warnings:
        print(f"warning: {warning}")


def main() -> int:
    # Keep the legacy single-source override for operators, but exercise GFS by
    # default because it is the first deterministic option in Telegram.
    legacy_source = os.getenv("METEOGRAM_SMOKE_SOURCE", "").strip()
    raw_sources = legacy_source or os.getenv(
        "METEOGRAM_SMOKE_SOURCES", "gfs,icon_eps"
    )
    source_ids = [value.strip() for value in raw_sources.split(",") if value.strip()]
    if not source_ids:
        raise RuntimeError("METEOGRAM_SMOKE_SOURCES не содержит источников")
    for source_id in source_ids:
        _check_source(source_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
