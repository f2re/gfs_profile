from __future__ import annotations

"""Live contract smoke for the Open-Meteo ensemble used by Telegram."""

import os

import numpy as np

from meteogram_fetch import fetch_meteogram


def main() -> int:
    source_id = os.getenv("METEOGRAM_SMOKE_SOURCE", "icon_eps")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
