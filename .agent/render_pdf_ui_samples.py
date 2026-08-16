from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np

from meteogram_report import write_meteogram_report
from meteogram_report_smoke import _Source, _series


def _chart(path: Path) -> None:
    x = np.arange(0, 72, 3)
    fig, axes = plt.subplots(4, 1, figsize=(15.5, 9.0), sharex=True)
    fig.suptitle("Санкт-Петербург · ECMWF AIFS 0.25°", fontsize=18, fontweight="bold", x=0.06, ha="left")
    axes[0].plot(x, 14 + 4 * np.sin(x / 10))
    axes[0].set_ylabel("°C")
    axes[0].set_title("Температура", loc="left", fontsize=10)
    axes[1].plot(x, 75 + 18 * np.sin(x / 8 + 1))
    axes[1].set_ylim(0, 100)
    axes[1].set_ylabel("%")
    axes[1].set_title("Влажность и облачность", loc="left", fontsize=10)
    axes[2].bar(x, np.maximum(0, 0.8 * np.sin(x / 6)), width=2.6, alpha=0.55)
    axes[2].set_ylabel("мм")
    axes[2].set_title("Осадки", loc="left", fontsize=10)
    axes[3].plot(x, 3 + 2 * np.cos(x / 9))
    axes[3].set_ylabel("м/с")
    axes[3].set_title("Ветер", loc="left", fontsize=10)
    for ax in axes:
        ax.grid(True, alpha=0.2)
    axes[-1].set_xlabel("местное время")
    fig.tight_layout(rect=(0.04, 0.04, 0.98, 0.94))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _deterministic():
    series = _series()
    series.source = _Source(
        source_id="ecmwf_aifs",
        label="ECMWF AIFS",
        model="ECMWF AIFS 0.25°",
        provider="ECMWF через Open-Meteo",
        resolution="0.25°",
        ensemble=False,
    )
    series.point_label = "Санкт-Петербург"
    series.member_count = None
    series.expected_member_count = None
    return series


def _ensemble():
    series = _series()
    series.point_label = "Мурманск"
    return series


def main() -> None:
    out = Path("ui_artifacts")
    out.mkdir(exist_ok=True)
    chart = out / "sample_meteogram.png"
    _chart(chart)
    for name, series in (("deterministic", _deterministic()), ("ensemble", _ensemble())):
        result = write_meteogram_report(
            series,
            chart,
            "pdf",
            output_dir=out,
            pdf_fallback_to_docx=False,
        )
        result.path.replace(out / f"{name}.pdf")
    print("UI samples created")


if __name__ == "__main__":
    main()
