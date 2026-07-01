from __future__ import annotations

import math
import tempfile
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from windgram_product import WindgramData, windgram_matrices

WIND_SPEED_BOUNDS_MS = (0, 3, 6, 10, 15, 20, 25, 30, 40, 60)


def _safe_suffix(data: WindgramData) -> str:
    suffix = f"_{data.run.date}_{data.run.cycle}_f{data.leads[0]:03d}_to_f{data.leads[-1]:03d}_{data.grid_lat:.3f}_{data.grid_lon:.3f}.png"
    return suffix.replace("-", "m").replace(" ", "_")


def _flow_arrow_components(wind_dir_deg: float) -> tuple[float, float]:
    """Return screen-oriented unit vector for where the wind flows.

    wind_dir_deg is meteorological FROM direction. The arrow shows TO direction.
    In the rendered axis y is inverted, so north/up is negative data-y.
    """

    to_dir = (wind_dir_deg + 180.0) % 360.0
    radians = math.radians(to_dir)
    dx = math.sin(radians)
    dy = -math.cos(radians)
    return dx, dy


def _text_color_for_value(value: float, bounds: tuple[int, ...] = WIND_SPEED_BOUNDS_MS) -> str:
    if value >= 20:
        return "white"
    return "black"


def _height_labels_by_level(data: WindgramData) -> dict[int, str]:
    heights: dict[int, list[float]] = defaultdict(list)
    for cell in data.cells:
        if cell.height_m is not None:
            heights[cell.pressure_hpa].append(cell.height_m)

    labels: dict[int, str] = {}
    for level in data.levels_hpa:
        values = heights.get(level) or []
        if not values:
            labels[level] = f"{level}"
            continue
        values_sorted = sorted(values)
        median = values_sorted[len(values_sorted) // 2]
        labels[level] = f"{level}\n{median / 1000.0:.1f} км"
    return labels


def _x_tick_labels(data: WindgramData) -> list[str]:
    labels: list[str] = []
    dense = len(data.leads) > 25
    for lead in data.leads:
        if dense:
            labels.append(f"+{lead}")
        else:
            valid = data.run.run_datetime_utc + timedelta(hours=lead)
            labels.append(f"+{lead}\n{valid:%d.%m\n%HZ}")
    return labels


def write_windgram_png(data: WindgramData) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm

    plt.rcParams.setdefault("font.family", "DejaVu Sans")

    speed, direction, _, _ = windgram_matrices(data)

    levels_top_down = list(reversed(data.levels_hpa))
    height_labels = _height_labels_by_level(data)
    speed_plot = speed[::-1, :]
    direction_plot = direction[::-1, :]
    n_levels, n_leads = speed_plot.shape

    cell_width = 0.62 if n_leads <= 25 else 0.42
    fig_width = max(11.5, n_leads * cell_width)
    fig_height = max(7.0, n_levels * 0.52)

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_windgram", suffix=_safe_suffix(data), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        cmap = plt.get_cmap("turbo")
        norm = BoundaryNorm(WIND_SPEED_BOUNDS_MS, cmap.N, clip=True)
        image = ax.imshow(speed_plot, cmap=cmap, norm=norm, aspect="auto", origin="upper")

        ax.set_xticks(range(n_leads))
        ax.set_xticklabels(_x_tick_labels(data), rotation=90 if n_leads > 25 else 0, fontsize=7 if n_leads > 25 else 8)
        ax.set_yticks(range(n_levels))
        ax.set_yticklabels([height_labels[level] for level in levels_top_down], fontsize=8)
        ax.set_xlabel("Срок прогноза и UTC-время")
        ax.set_ylabel("Изобарический уровень p, гПа / геопотенциальная высота Z, км")
        ax.set_title(
            f"GFS 0.25 · ветер по срокам и изобарическим уровням · {data.run.date} {data.run.cycle}Z · "
            f"+{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}",
            fontsize=10,
        )

        ax.set_xticks([x - 0.5 for x in range(1, n_leads)], minor=True)
        ax.set_yticks([y - 0.5 for y in range(1, n_levels)], minor=True)
        ax.grid(which="minor", linewidth=0.45, color="white", alpha=0.88)
        ax.tick_params(which="minor", bottom=False, left=False)

        for y in range(n_levels):
            for x in range(n_leads):
                value = speed_plot[y, x]
                wind_dir = direction_plot[y, x]
                if math.isnan(value):
                    continue
                color = _text_color_for_value(float(value))
                ax.text(x, y + 0.19, f"{value:.0f}", ha="center", va="center", fontsize=7, color=color, fontweight="bold")
                if not math.isnan(wind_dir):
                    dx, dy = _flow_arrow_components(float(wind_dir))
                    length = 0.30 if n_leads <= 25 else 0.24
                    start = (x - dx * length / 2.0, y - 0.18 - dy * length / 2.0)
                    end = (x + dx * length / 2.0, y - 0.18 + dy * length / 2.0)
                    ax.annotate(
                        "",
                        xy=end,
                        xytext=start,
                        arrowprops={"arrowstyle": "-|>", "lw": 0.85, "color": color, "shrinkA": 0, "shrinkB": 0},
                    )

        ax.set_xlim(-0.5, n_leads - 0.5)
        ax.set_ylim(n_levels - 0.5, -0.5)
        colorbar = fig.colorbar(image, ax=ax, pad=0.012, boundaries=WIND_SPEED_BOUNDS_MS, ticks=WIND_SPEED_BOUNDS_MS)
        colorbar.set_label("Скорость ветра V, м/с")
        fig.text(
            0.5,
            0.012,
            "Стрелка в ячейке показывает направление переноса воздуха; число — скорость ветра V, м/с. Данные: модельный профиль GFS, не радиозонд.",
            ha="center",
            fontsize=8,
        )
        fig.tight_layout(rect=(0, 0.045, 1, 1))
        fig.savefig(out_path, dpi=170, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
