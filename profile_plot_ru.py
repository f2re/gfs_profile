from __future__ import annotations

import tempfile
from pathlib import Path

from gfs_core import ProfileResult

PRESSURE_TICKS = (1000, 925, 850, 700, 500, 300, 200, 100)
WIND_LEVELS = (1000, 925, 850, 700, 500, 300, 200)


def _safe_suffix(result: ProfileResult) -> str:
    suffix = f"_{result.run.date}_{result.run.cycle}_f{result.lead_hour:03d}_{result.grid_lat:.3f}_{result.grid_lon:.3f}.png"
    return suffix.replace("-", "m").replace(" ", "_")


def _height_km(row) -> float:
    return float(row.get("geopotential_height_km", float(row["geopotential_height_m"]) / 1000.0))


def _setup_pressure_axis(axis, df) -> None:
    axis.set_yscale("log")
    axis.set_ylim(1050, 100)
    axis.set_yticks(PRESSURE_TICKS)
    labels = []
    for level in PRESSURE_TICKS:
        idx = (df["pressure_hpa"] - level).abs().idxmin()
        row = df.loc[idx]
        if abs(float(row["pressure_hpa"]) - level) <= 35.0:
            labels.append(f"{level}\n{_height_km(row):.1f} км")
        else:
            labels.append(str(level))
    axis.set_yticklabels(labels)
    axis.grid(True, linewidth=0.4, which="both")


def _nearest_rows(df, levels: tuple[int, ...], tolerance_hpa: float = 35.0):
    rows = []
    for level in levels:
        idx = (df["pressure_hpa"] - level).abs().idxmin()
        row = df.loc[idx]
        if abs(float(row["pressure_hpa"]) - level) <= tolerance_hpa:
            rows.append(row)
    return rows


def write_profile_png(result: ProfileResult) -> Path:
    import matplotlib.pyplot as plt

    df = result.dataframe.dropna(subset=["pressure_hpa"]).sort_values("pressure_hpa", ascending=False).copy()
    if df.empty:
        raise ValueError("Пустой профиль: нечего строить")

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_profile", suffix=_safe_suffix(result), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        pressure = df["pressure_hpa"]
        fig, axes = plt.subplots(1, 4, figsize=(13, 7), sharey=True)
        fig.suptitle(
            f"Профиль GFS 0.25 | запуск {result.run.date} {result.run.cycle}Z | "
            f"срок +{result.lead_hour} ч | узел {result.grid_lat:.2f}, {result.grid_lon:.2f}",
            fontsize=11,
        )

        axes[0].plot(df["temperature_c"], pressure, label="Температура")
        if "dewpoint_c" in df:
            axes[0].plot(df["dewpoint_c"], pressure, label="Точка росы")
        axes[0].axvline(0, linewidth=0.8)
        axes[0].set_xlabel("Температура, °C")
        axes[0].set_ylabel("Давление, гПа")
        axes[0].set_title("Температура")
        axes[0].legend(loc="best", fontsize=8)

        axes[1].plot(df["relative_humidity_pct"], pressure)
        axes[1].set_xlabel("Отн. влажность, %")
        axes[1].set_title("Влажность")
        axes[1].set_xlim(0, 100)

        axes[2].plot(df["wind_speed_ms"], pressure)
        axes[2].set_xlabel("Скорость, м/с")
        axes[2].set_title("Скорость ветра")

        rows = _nearest_rows(df, WIND_LEVELS)
        if rows:
            x = [0.5] * len(rows)
            p = [float(row["pressure_hpa"]) for row in rows]
            u = [float(row["u_wind_ms"]) for row in rows]
            v = [float(row["v_wind_ms"]) for row in rows]
            axes[3].barbs(x, p, u, v, length=6, linewidth=0.7)
            for row in rows:
                axes[3].text(
                    0.72,
                    float(row["pressure_hpa"]),
                    f"{_height_km(row):.1f} км  {int(round(float(row['wind_dir_deg']))) % 360:03d}° / {float(row['wind_speed_ms']):.1f}",
                    va="center",
                    fontsize=8,
                )
        axes[3].set_xlim(0, 1.35)
        axes[3].set_xticks([])
        axes[3].set_xlabel("Высота, направление / м/с")
        axes[3].set_title("Ветер")

        for axis in axes:
            _setup_pressure_axis(axis, df)

        fig.text(
            0.5,
            0.01,
            f"Действительно на {result.valid_time_utc:%Y-%m-%d %H:%M UTC}. Модельная точка GFS, не радиозонд.",
            ha="center",
            fontsize=9,
        )
        fig.tight_layout(rect=(0, 0.04, 1, 0.93))
        fig.savefig(out_path, dpi=140, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
