from __future__ import annotations

import math
import tempfile
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from plot_style import (
    METEO,
    add_footer,
    apply_meteo_rcparams,
    humidity_cmap_and_norm,
    style_axis,
    temperature_cmap_and_norm,
    value_text_color,
    wind_speed_cmap_and_norm,
)
from time_guides_plot import draw_utc_day_guides
from windgram_product import WindgramData, normalize_windgram_param, windgram_matrices

PARAM_TITLES = {
    "wind": "скорость ветра V",
    "temp": "температура T",
    "rh": "относительная влажность RH",
}
PARAM_COLORBAR_LABELS = {
    "wind": "Скорость ветра V, м/с",
    "temp": "Температура T, °C",
    "rh": "Относительная влажность RH, %",
}
PARAM_FOOTERS = {
    "wind": "Цвет и число — скорость ветра V, м/с; стрелка показывает направление переноса воздуха.",
    "temp": "Цвет и число — температура T, °C; стрелка показывает направление переноса воздуха.",
    "rh": "Цвет и число — относительная влажность RH, %; стрелка показывает направление переноса воздуха.",
}


def _safe_suffix(data: WindgramData, param: str) -> str:
    suffix = f"_{param}_{data.run.date}_{data.run.cycle}_f{data.leads[0]:03d}_to_f{data.leads[-1]:03d}_{data.grid_lat:.3f}_{data.grid_lon:.3f}.png"
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


def _mean_height_km(values: list[float]) -> float | None:
    finite = np.asarray([value for value in values if value is not None and np.isfinite(value)], dtype=float)
    if finite.size == 0:
        return None
    return float(np.nanmean(finite) / 1000.0)


def _height_labels_by_level(data: WindgramData) -> dict[int, str]:
    """Return y-axis labels with mean geopotential height for each pressure level.

    The mean is calculated over all forecast leads included in the windgram.
    This avoids static standard-atmosphere labels and keeps wind/temp/RH
    windgrams consistent with the actual GFS thickness field for the selected
    point, run and lead range.
    """

    heights: dict[int, list[float]] = defaultdict(list)
    for cell in data.cells:
        if cell.height_m is not None and np.isfinite(cell.height_m):
            heights[cell.pressure_hpa].append(float(cell.height_m))

    labels: dict[int, str] = {}
    for level in data.levels_hpa:
        mean_km = _mean_height_km(heights.get(level) or [])
        if mean_km is None:
            labels[level] = f"{level}\nZср —"
        else:
            labels[level] = f"{level}\nZср {mean_km:.1f} км"
    return labels


def _x_tick_labels(data: WindgramData) -> list[str]:
    labels: list[str] = []
    dense = len(data.leads) > 25
    for lead in data.leads:
        if dense:
            labels.append(f"+{lead}")
        else:
            valid = data.run.run_datetime_utc + timedelta(hours=lead)
            labels.append(f"+{lead}\n{valid:%d.%m}\n{valid:%HZ}")
    return labels


def _format_cell_value(value: float, param: str) -> str:
    if param == "temp":
        return f"{value:.0f}"
    if param == "rh":
        return f"{value:.0f}"
    return f"{value:.0f}"


def _annotate_cell(ax, x: int, y: int, value: float, wind_dir: float, n_leads: int, param: str) -> None:
    color = value_text_color(value, param=param)
    shadow_color = "#FFFFFF" if color != "#FFFFFF" else "#102033"
    label = _format_cell_value(value, param)
    ax.text(x, y + 0.20, label, ha="center", va="center", fontsize=7, color=shadow_color, fontweight="bold", alpha=0.42)
    ax.text(x, y + 0.18, label, ha="center", va="center", fontsize=7, color=color, fontweight="bold")
    if not math.isnan(wind_dir):
        dx, dy = _flow_arrow_components(float(wind_dir))
        length = 0.32 if n_leads <= 25 else 0.25
        start = (x - dx * length / 2.0, y - 0.18 - dy * length / 2.0)
        end = (x + dx * length / 2.0, y - 0.18 + dy * length / 2.0)
        ax.annotate(
            "",
            xy=end,
            xytext=start,
            arrowprops={"arrowstyle": "-|>", "lw": 1.05, "color": color, "shrinkA": 0, "shrinkB": 0, "mutation_scale": 8},
        )


def _param_matrix_and_style(data: WindgramData, param: str):
    speed, direction, _, _, temperature, humidity = windgram_matrices(data)
    if param == "temp":
        cmap, norm = temperature_cmap_and_norm()
        return temperature, direction, cmap, norm
    if param == "rh":
        cmap, norm = humidity_cmap_and_norm()
        return humidity, direction, cmap, norm
    cmap, norm = wind_speed_cmap_and_norm()
    return speed, direction, cmap, norm


def write_windgram_png(data: WindgramData, param: str | None = None) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    selected_param = normalize_windgram_param(param or data.param)
    apply_meteo_rcparams(plt)

    value_matrix, direction, cmap, norm = _param_matrix_and_style(data, selected_param)

    levels_top_down = list(reversed(data.levels_hpa))
    height_labels = _height_labels_by_level(data)
    value_plot = value_matrix[::-1, :]
    direction_plot = direction[::-1, :]
    n_levels, n_leads = value_plot.shape

    cell_width = 0.64 if n_leads <= 25 else 0.43
    fig_width = max(12.0, n_leads * cell_width)
    fig_height = max(7.4, n_levels * 0.55)

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_windgram", suffix=_safe_suffix(data, selected_param), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height), facecolor=METEO.figure_bg)
        image = ax.imshow(value_plot, cmap=cmap, norm=norm, aspect="auto", origin="upper", interpolation="nearest")

        ax.set_xticks(range(n_leads))
        ax.set_xticklabels(_x_tick_labels(data), rotation=90 if n_leads > 25 else 0, fontsize=7 if n_leads > 25 else 8)
        ax.set_yticks(range(n_levels))
        ax.set_yticklabels([height_labels[level] for level in levels_top_down], fontsize=8)
        ax.set_xlabel("Срок прогноза и UTC-время")
        ax.set_ylabel("Изобарический уровень p, гПа / средняя по срокам геопотенциальная высота Zср, км")
        ax.set_title(
            f"GFS 0.25 · windgram: {PARAM_TITLES[selected_param]} по срокам и уровням · {data.run.date} {data.run.cycle}Z · "
            f"+{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}",
            fontsize=10.5,
            fontweight="bold",
            color=METEO.axis_text,
            pad=12,
        )

        style_axis(ax, grid=False)
        draw_utc_day_guides(ax, data.cells, -0.86, fontsize=7.4 if n_leads > 25 else 8.0)
        ax.set_xticks([x - 0.5 for x in range(1, n_leads)], minor=True)
        ax.set_yticks([y - 0.5 for y in range(1, n_levels)], minor=True)
        ax.grid(which="minor", linewidth=0.65, color="#FFFFFF", alpha=0.92)
        ax.tick_params(which="minor", bottom=False, left=False)

        for y in range(n_levels):
            for x in range(n_leads):
                value = value_plot[y, x]
                wind_dir = direction_plot[y, x]
                if math.isnan(value):
                    continue
                _annotate_cell(ax, x, y, float(value), float(wind_dir), n_leads, selected_param)

        ax.set_xlim(-0.5, n_leads - 0.5)
        ax.set_ylim(n_levels - 0.5, -0.5)
        colorbar = fig.colorbar(image, ax=ax, pad=0.012, fraction=0.032)
        colorbar.set_label(PARAM_COLORBAR_LABELS[selected_param], color=METEO.axis_text)
        colorbar.ax.tick_params(labelsize=8, colors=METEO.axis_text)
        colorbar.outline.set_edgecolor(METEO.spine)
        colorbar.outline.set_linewidth(0.8)

        add_footer(fig, PARAM_FOOTERS[selected_param] + " Zср слева — средняя геопотенциальная высота уровня по всем срокам диаграммы. Данные: модельный профиль GFS.", y=0.012)
        fig.tight_layout(rect=(0, 0.052, 1, 1))
        fig.savefig(out_path, dpi=180, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
