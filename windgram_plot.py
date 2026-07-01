from __future__ import annotations

import math
import tempfile
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


def write_windgram_png(data: WindgramData) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm

    speed, direction, _, _ = windgram_matrices(data)

    levels_top_down = list(reversed(data.levels_hpa))
    speed_plot = speed[::-1, :]
    direction_plot = direction[::-1, :]
    n_levels, n_leads = speed_plot.shape

    cell_width = 0.58 if n_leads <= 25 else 0.38
    fig_width = max(11.0, n_leads * cell_width)
    fig_height = max(6.5, n_levels * 0.46)

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_windgram", suffix=_safe_suffix(data), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        cmap = plt.get_cmap("viridis")
        norm = BoundaryNorm(WIND_SPEED_BOUNDS_MS, cmap.N, clip=True)
        image = ax.imshow(speed_plot, cmap=cmap, norm=norm, aspect="auto", origin="upper")

        ax.set_xticks(range(n_leads))
        ax.set_xticklabels([f"+{lead}" for lead in data.leads], rotation=90 if n_leads > 25 else 0, fontsize=8)
        ax.set_yticks(range(n_levels))
        ax.set_yticklabels([str(level) for level in levels_top_down], fontsize=8)
        ax.set_xlabel("Срок прогноза, ч")
        ax.set_ylabel("Давление, гПа")
        ax.set_title(
            f"GFS 0.25 ветер × время | {data.run.date} {data.run.cycle}Z | "
            f"+{data.leads[0]}…+{data.leads[-1]} ч | узел {data.grid_lat:.2f}, {data.grid_lon:.2f}",
            fontsize=10,
        )

        ax.set_xticks([x - 0.5 for x in range(1, n_leads)], minor=True)
        ax.set_yticks([y - 0.5 for y in range(1, n_levels)], minor=True)
        ax.grid(which="minor", linewidth=0.45, color="white", alpha=0.85)
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
                    length = 0.28
                    start = (x - dx * length / 2.0, y - 0.18 - dy * length / 2.0)
                    end = (x + dx * length / 2.0, y - 0.18 + dy * length / 2.0)
                    ax.annotate(
                        "",
                        xy=end,
                        xytext=start,
                        arrowprops={"arrowstyle": "-|>", "lw": 0.8, "color": color, "shrinkA": 0, "shrinkB": 0},
                    )

        ax.set_xlim(-0.5, n_leads - 0.5)
        ax.set_ylim(n_levels - 0.5, -0.5)
        colorbar = fig.colorbar(image, ax=ax, pad=0.012, boundaries=WIND_SPEED_BOUNDS_MS)
        colorbar.set_label("Скорость ветра, м/с")
        fig.text(0.5, 0.01, "Стрелка показывает направление переноса воздуха; число в ячейке — скорость ветра, м/с.", ha="center", fontsize=8)
        fig.tight_layout(rect=(0, 0.035, 1, 1))
        fig.savefig(out_path, dpi=160, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
