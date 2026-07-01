from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cloudgram_product import CloudgramCell, CloudgramData
from plot_style import (
    METEO,
    PRECIP_TYPE_COLORS,
    add_footer,
    apply_meteo_rcparams,
    cb_cmap_and_norm,
    ceiling_cmap_and_norm,
    cloud_cover_cmap_and_norm,
    precip_cmap_and_norm,
    style_axis,
    value_text_color,
)


@dataclass(frozen=True)
class CloudgramRow:
    key: str
    label: str
    unit: str


CLOUDGRAM_ROWS = (
    CloudgramRow("high", "Высокая", "%"),
    CloudgramRow("mid", "Средняя", "%"),
    CloudgramRow("low", "Низкая", "%"),
    CloudgramRow("total", "Общая", "%"),
    CloudgramRow("precip", "Осадки", "мм"),
    CloudgramRow("ptype", "Тип", "код"),
    CloudgramRow("cb", "Гроза", "0–3"),
    CloudgramRow("ceiling", "ВНГО", "м"),
)


def _safe_suffix(data: CloudgramData) -> str:
    suffix = f"_{data.run.date}_{data.run.cycle}_f{data.leads[0]:03d}_to_f{data.leads[-1]:03d}_{data.grid_lat:.3f}_{data.grid_lon:.3f}.png"
    return suffix.replace("-", "m").replace(" ", "_")


def _cloud_value(cell: CloudgramCell, key: str) -> float | None:
    if key == "high":
        return cell.high_cloud_pct
    if key == "mid":
        return cell.mid_cloud_pct
    if key == "low":
        return cell.low_cloud_pct
    if key == "total":
        return cell.total_cloud_pct
    return None


def _format_precip(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 0.05:
        return "0"
    if value < 10:
        return f"{value:.1f}"
    return f"{value:.0f}"


def _format_ceiling(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 2000:
        return f"{value / 1000.0:.1f}к"
    return f"{value:.0f}"


def _cell_color_and_text(row: CloudgramRow, cell: CloudgramCell):
    if row.key in {"high", "mid", "low", "total"}:
        value = _cloud_value(cell, row.key)
        cmap, norm = cloud_cover_cmap_and_norm()
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return cmap(norm(value)), f"{value:.0f}", value_text_color(value, param="cloud")

    if row.key == "precip":
        value = cell.precip_mm
        cmap, norm = precip_cmap_and_norm()
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return cmap(norm(value)), _format_precip(value), value_text_color(value, param="precip")

    if row.key == "ptype":
        text = cell.precip_type or "—"
        base = text.split("/", 1)[0]
        return PRECIP_TYPE_COLORS.get(base, "#F3F4F6"), text, METEO.axis_text

    if row.key == "cb":
        value = float(cell.cb_score)
        cmap, norm = cb_cmap_and_norm()
        return cmap(norm(value)), f"{int(value)}", value_text_color(value, param="cb")

    if row.key == "ceiling":
        value = cell.ceiling_m
        cmap, norm = ceiling_cmap_and_norm()
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return cmap(norm(value)), _format_ceiling(value), value_text_color(value, param="ceiling")

    return "#FFFFFF", "", METEO.axis_text


def _tick_labels(data: CloudgramData) -> list[str]:
    dense = len(data.leads) > 25
    labels: list[str] = []
    for lead, cell in zip(data.leads, data.cells):
        if dense:
            labels.append(f"+{lead}")
        else:
            labels.append(f"+{lead}\n{cell.valid_time_utc:%d.%m}\n{cell.valid_time_utc:%HZ}")
    return labels


def write_cloudgram_png(data: CloudgramData) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    apply_meteo_rcparams(plt)
    n_cols = len(data.cells)
    n_rows = len(CLOUDGRAM_ROWS)
    fig_width = max(12.0, n_cols * (0.62 if n_cols <= 25 else 0.42))
    fig_height = 6.9

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_cloudgram", suffix=_safe_suffix(data), delete=False)
    out_path = Path(tmp.name)
    tmp.close()

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height), facecolor=METEO.figure_bg)
        ax.set_facecolor(METEO.axes_bg)

        for y, row in enumerate(CLOUDGRAM_ROWS):
            for x, cell in enumerate(data.cells):
                facecolor, text, text_color = _cell_color_and_text(row, cell)
                ax.add_patch(Rectangle((x - 0.5, y - 0.5), 1.0, 1.0, facecolor=facecolor, edgecolor="#FFFFFF", linewidth=0.8))
                ax.text(x, y, text, ha="center", va="center", fontsize=7.4, color=text_color, fontweight="bold")

        ax.set_xlim(-0.5, n_cols - 0.5)
        ax.set_ylim(n_rows - 0.5, -0.5)
        ax.set_xticks(range(n_cols))
        ax.set_xticklabels(_tick_labels(data), rotation=90 if n_cols > 25 else 0, fontsize=7 if n_cols > 25 else 8)
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels([f"{row.label}\n{row.unit}" for row in CLOUDGRAM_ROWS], fontsize=8)
        ax.set_xlabel("Срок прогноза и UTC-время")
        ax.set_ylabel("Параметр")
        ax.set_title(
            f"GFS 0.25 · cloudgram: облачность, осадки, грозовой риск и ВНГО · {data.run.date} {data.run.cycle}Z · "
            f"+{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}",
            fontsize=10.5,
            fontweight="bold",
            color=METEO.axis_text,
            pad=12,
        )

        style_axis(ax, grid=False)
        ax.tick_params(which="both", length=0)
        missing = f" Пропущено: {', '.join(data.missing_fields)}." if data.missing_fields else ""
        add_footer(
            fig,
            "Облачность: значение покрытия %, осадки: мм за срок, гроза: proxy 0–3, ВНГО: модельный ceiling. Осадки показаны зелёной шкалой." + missing,
            y=0.012,
        )
        fig.tight_layout(rect=(0, 0.055, 1, 1))
        fig.savefig(out_path, dpi=180, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path
