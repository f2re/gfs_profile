from __future__ import annotations

import tempfile
from pathlib import Path

from cloudgram_product import CloudgramCell, CloudgramData
from cloudgram_plot import (
    CloudgramRow,
    PRO_ROWS as LEGACY_PRO_ROWS,
    SIMPLE_ROWS,
    _draw_cloud_layers_cell,
    _draw_icon,
    _hour_lead_labels,
    _pro_cell,
    _set_icon_y_scale,
    _simple_cell,
)
from plot_style import METEO, add_footer, apply_meteo_rcparams, style_axis
from time_guides_plot import draw_utc_day_guides


PRO_ROWS = tuple(
    CloudgramRow(
        row.key,
        "ВНГО AGL" if row.key == "ceiling" else "Конвект.\nпотенциал" if row.key == "cb" else row.label,
        "м над землёй" if row.key == "ceiling" else row.unit,
    )
    for row in LEGACY_PRO_ROWS
)


def _audited_simple_cell(row: CloudgramRow, cell: CloudgramCell):
    if row.key != "storm_simple":
        return _simple_cell(row, cell)
    if cell.phenomena == "TSRA":
        return "#DC2626", "icon:storm_3", "#FFFFFF"
    if int(cell.cb_score) >= 2:
        return "#EEE5FF", "пот.", "#6D28D9"
    if int(cell.cb_score) == 1:
        return "#F6F2FF", "слаб.", "#7C3AED"
    return "#FFFFFF", "", METEO.axis_text


def _pro_footer(data: CloudgramData) -> str:
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    missing = f" Нет полей: {', '.join(data.missing_fields)}." if data.missing_fields else ""
    intervals = sorted({float(cell.precip_interval_hours) for cell in data.cells if cell.precip_interval_hours is not None})
    interval_text = "/".join(f"{value:g}" for value in intervals) if intervals else "—"
    return (
        "Облачность H/M/L и TCDC выбраны по точным слоям GFS. "
        f"Осадки — количество за интервал {interval_text} ч. ВНГО — HGT cloud ceiling минус HGT поверхности, AGL. "
        "Конвективный потенциал 0–3 рассчитан по CAPE/CIN 180–0 гПа AGL, ACPCP, CPRAT и TCDC convective cloud; "
        "TSRA/⚡ только при уровне 3 и осадках. Шкала опасности 0–4 локальная, не ICAO. "
        f"Макс. опасность: {max_hazard}.{missing}"
    )


def _simple_footer(data: CloudgramData) -> str:
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    return (
        "Облака: верх/сред/низ; осадки — количество за интервал. "
        "ВНГО используется только как AGL. «пот.» — конвективный потенциал, не гроза; ⚡ — только модельный TSRA.\n"
        "Опасность 0–4 — локальная модельная шкала по осадкам, VIS, ВНГО AGL и подтверждённому TSRA. "
        f"Максимум: {max_hazard}."
    )


def _write_grid(data: CloudgramData, rows, cell_func, *, title: str, footer: str, simple: bool) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    apply_meteo_rcparams(plt)
    n_cols = len(data.cells)
    n_rows = len(rows)
    fig_width = max(10.0, n_cols * (0.54 if not simple else 0.44))
    fig_height = 4.25 if simple else 6.7

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_cloudgram", suffix=".png", delete=False)
    out_path = Path(tmp.name)
    tmp.close()
    fig = None
    try:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height), facecolor=METEO.figure_bg)
        ax.set_facecolor(METEO.axes_bg)
        _set_icon_y_scale(ax, fig_width=fig_width, fig_height=fig_height, n_cols=n_cols, n_rows=n_rows)
        for y, row in enumerate(rows):
            for x, cell in enumerate(data.cells):
                if row.key == "cloud_layers":
                    _draw_cloud_layers_cell(ax, x, y, cell, Rectangle, simple=False)
                    continue
                if row.key == "cloud_simple":
                    _draw_cloud_layers_cell(ax, x, y, cell, Rectangle, simple=True)
                    continue
                facecolor, text, text_color = cell_func(row, cell)
                line_width = 1.05 if row.key in {"hazard", "hazard_simple"} else 0.75
                ax.add_patch(Rectangle((x - 0.5, y - 0.5), 1, 1, facecolor=facecolor, edgecolor="#FFFFFF", linewidth=line_width))
                if text.startswith("icon:"):
                    _draw_icon(ax, text.split(":", 1)[1], x, y)
                elif text:
                    ax.text(x, y, text, ha="center", va="center", fontsize=8.0 if simple else 7.2, color=text_color, fontweight="bold")

        y_date = n_rows + (0.08 if simple else 0.22)
        draw_utc_day_guides(ax, data.cells, y_date, fontsize=7.4 if simple else 8.0)
        if not simple:
            for y in (0.5, 2.5, 4.5):
                ax.axhline(y, color="#53687D", linewidth=1.05, alpha=0.72)
        ax.set_xlim(-0.5, n_cols - 0.5)
        ax.set_ylim(n_rows + (0.34 if simple else 0.58), -0.5)
        ax.set_xticks(range(n_cols))
        ax.set_xticklabels(_hour_lead_labels(data, sparse=n_cols > 60), rotation=0, fontsize=7)
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels([f"{row.label}\n{row.unit}" if row.unit else row.label for row in rows], fontsize=8)
        ax.set_xlabel("" if simple else "UTC-время; ниже — заблаговременность +ч")
        ax.set_ylabel("Параметр")
        ax.set_title(title, fontsize=10.5, fontweight="bold", color=METEO.axis_text, pad=12)
        style_axis(ax, grid=False)
        ax.tick_params(which="both", length=0)
        if simple:
            fig.text(0.5, 0.017, footer, ha="center", va="bottom", fontsize=6.15, color=METEO.muted_text, linespacing=1.04)
        else:
            add_footer(fig, footer, y=0.012)
        fig.tight_layout(rect=(0, 0.080 if simple else 0.10, 1, 1))
        fig.savefig(out_path, dpi=180, bbox_inches="tight")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if fig is not None:
            plt.close(fig)
    return out_path


def _write_cloudgram_pro_png(data: CloudgramData) -> Path:
    title = (
        f"GFS 0.25 · cloudgram PRO: облачность, осадки, явления, VIS, ВНГО AGL и конвективный потенциал · "
        f"{data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}"
    )
    return _write_grid(data, PRO_ROWS, _pro_cell, title=title, footer=_pro_footer(data), simple=False)


def _write_cloudgram_simple_png(data: CloudgramData) -> Path:
    title = (
        f"GFS 0.25 · cloudgram SIMPLE: облака, осадки/явления, конвективный потенциал и видимость · "
        f"{data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}"
    )
    return _write_grid(data, SIMPLE_ROWS, _audited_simple_cell, title=title, footer=_simple_footer(data), simple=True)


def write_cloudgram_png(data: CloudgramData, mode: str = "pro") -> Path:
    if mode == "simple":
        return _write_cloudgram_simple_png(data)
    return _write_cloudgram_pro_png(data)
