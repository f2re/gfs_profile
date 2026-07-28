from __future__ import annotations

import tempfile
from pathlib import Path

from gfs_core import ProfileResult
from plot_style import METEO, add_footer, annotation_box_kwargs, apply_meteo_rcparams, style_axis

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
    style_axis(axis)


def _nearest_rows(df, levels: tuple[int, ...], tolerance_hpa: float = 35.0):
    rows = []
    for level in levels:
        idx = (df["pressure_hpa"] - level).abs().idxmin()
        row = df.loc[idx]
        if abs(float(row["pressure_hpa"]) - level) <= tolerance_hpa:
            rows.append(row)
    return rows


def _profile_title(result: ProfileResult) -> str:
    return (
        f"GFS 0.25 · вертикальный профиль · запуск {result.run.date} {result.run.cycle}Z · "
        f"срок +{result.lead_hour} ч · узел {result.grid_lat:.2f}, {result.grid_lon:.2f}"
    )


def _diagnostic_text(result: ProfileResult, df) -> str:
    lines = [
        "Модельная точка GFS",
        f"действительно {result.valid_time_utc:%Y-%m-%d %H:%M UTC}",
    ]
    if "wind_speed_ms" in df:
        idx = df["wind_speed_ms"].idxmax()
        row = df.loc[idx]
        lines.append(f"Vmax {float(row['wind_speed_ms']):.0f} м/с @ {int(round(float(row['pressure_hpa'])))} гПа")
    return "\n".join(lines)


def write_profile_png(result: ProfileResult) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    apply_meteo_rcparams(plt)

    df = result.dataframe.dropna(subset=["pressure_hpa"]).sort_values("pressure_hpa", ascending=False).copy()
    if df.empty:
        raise ValueError("Пустой профиль: нечего строить")

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_profile", suffix=_safe_suffix(result), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        pressure = df["pressure_hpa"]
        fig, axes = plt.subplots(1, 4, figsize=(14.2, 7.6), sharey=True, facecolor=METEO.figure_bg)
        fig.suptitle(_profile_title(result), fontsize=12, fontweight="bold", color=METEO.axis_text)

        axes[0].plot(df["temperature_c"], pressure, label="T — температура", color=METEO.temperature, linewidth=2.35)
        if "dewpoint_c" in df:
            axes[0].plot(df["dewpoint_c"], pressure, label="Td — точка росы", color=METEO.dewpoint, linewidth=2.15)
        axes[0].axvline(0, linewidth=1.0, color=METEO.freezing, linestyle="--", alpha=0.82)
        axes[0].set_xlabel("T / Td, °C")
        axes[0].set_ylabel("p, гПа / Zg, км MSL")
        axes[0].set_title("Температура и точка росы")
        axes[0].legend(loc="best", fontsize=8)

        axes[1].plot(df["relative_humidity_pct"], pressure, color=METEO.humidity, linewidth=2.1)
        axes[1].fill_betweenx(pressure, 0, df["relative_humidity_pct"], color=METEO.humidity, alpha=0.18)
        axes[1].set_xlabel("RH, %")
        axes[1].set_title("Относительная влажность")
        axes[1].set_xlim(0, 100)

        axes[2].plot(df["wind_speed_ms"], pressure, color=METEO.wind, linewidth=2.2)
        axes[2].fill_betweenx(pressure, 0, df["wind_speed_ms"], color="#94A3B8", alpha=0.18)
        axes[2].set_xlabel("V, м/с")
        axes[2].set_title("Скорость ветра")

        rows = _nearest_rows(df, WIND_LEVELS)
        if rows:
            x = [0.42] * len(rows)
            p = [float(row["pressure_hpa"]) for row in rows]
            u = [float(row["u_wind_ms"]) for row in rows]
            v = [float(row["v_wind_ms"]) for row in rows]
            axes[3].barbs(x, p, u, v, length=6.3, linewidth=0.85, color=METEO.wind)
            for row in rows:
                axes[3].text(
                    0.66,
                    float(row["pressure_hpa"]),
                    f"{_height_km(row):.1f} км MSL  {int(round(float(row['wind_dir_deg']))) % 360:03d}° / {float(row['wind_speed_ms']):.1f}",
                    va="center",
                    fontsize=8,
                    color=METEO.axis_text,
                )
        axes[3].set_xlim(0, 1.55)
        axes[3].set_xticks([])
        axes[3].set_xlabel("Zg MSL · dd° / V м/с")
        axes[3].set_title("Ветер")
        axes[3].text(
            0.05,
            0.03,
            _diagnostic_text(result, df),
            transform=axes[3].transAxes,
            ha="left",
            va="bottom",
            fontsize=8,
            color=METEO.axis_text,
            bbox=annotation_box_kwargs(),
        )

        for axis in axes:
            _setup_pressure_axis(axis, df)

        add_footer(fig, "Модельный профиль GFS 0.25; Zg — геопотенциальная высота над средним уровнем моря (MSL), не AGL.")
        fig.tight_layout(rect=(0, 0.045, 1, 0.94))
        fig.savefig(out_path, dpi=170, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
