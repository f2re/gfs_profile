from __future__ import annotations

import tempfile
from pathlib import Path

from gfs_core import ProfileResult


def _safe_suffix(result: ProfileResult) -> str:
    suffix = f"_{result.run.date}_{result.run.cycle}_f{result.lead_hour:03d}_{result.grid_lat:.3f}_{result.grid_lon:.3f}.png"
    return suffix.replace("-", "m").replace(" ", "_")


def write_profile_png(result: ProfileResult) -> Path:
    """Render a compact PNG profile plot for Telegram delivery."""

    import matplotlib.pyplot as plt

    df = result.dataframe.sort_values("pressure_hpa", ascending=False).copy()
    if df.empty:
        raise ValueError("Пустой профиль: нечего строить")

    pressure = df["pressure_hpa"]
    tmp = tempfile.NamedTemporaryFile(prefix="gfs_profile", suffix=_safe_suffix(result), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig, axes = plt.subplots(1, 3, figsize=(11, 7), sharey=True)
    fig.suptitle(f"GFS 0.25 {result.run.date} {result.run.cycle}Z +{result.lead_hour} h")

    axes[0].plot(df["temperature_c"], pressure, label="T")
    if "dewpoint_c" in df:
        axes[0].plot(df["dewpoint_c"], pressure, label="Td")
    axes[0].axvline(0, linewidth=0.8)
    axes[0].set_xlabel("C")
    axes[0].set_ylabel("hPa")
    axes[0].set_title("T / Td")
    axes[0].legend(loc="best")

    axes[1].plot(df["relative_humidity_pct"], pressure)
    axes[1].set_xlabel("%")
    axes[1].set_title("RH")
    axes[1].set_xlim(0, 100)

    axes[2].plot(df["wind_speed_ms"], pressure)
    axes[2].set_xlabel("m/s")
    axes[2].set_title("Wind")

    for axis in axes:
        axis.grid(True, linewidth=0.4)
        axis.invert_yaxis()

    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out_path
