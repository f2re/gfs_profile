from __future__ import annotations

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


PRO_ROWS = (
    CloudgramRow("high", "Высокая", "%"),
    CloudgramRow("mid", "Средняя", "%"),
    CloudgramRow("low", "Низкая", "%"),
    CloudgramRow("total", "Общая", "%"),
    CloudgramRow("precip", "Осадки", "мм"),
    CloudgramRow("ptype", "Тип", "код"),
    CloudgramRow("phen", "Явления", ""),
    CloudgramRow("vis", "Видимость", "км"),
    CloudgramRow("ceiling", "ВНГО", "м"),
    CloudgramRow("cb", "Грозовой\nриск", "0–3"),
    CloudgramRow("hazard", "Опасность", "0–4"),
)

SIMPLE_ROWS = (
    CloudgramRow("cloud_simple", "Облака", ""),
    CloudgramRow("precip_simple", "Осадки", ""),
    CloudgramRow("storm_simple", "Гроза", ""),
    CloudgramRow("phen_simple", "Явления", ""),
    CloudgramRow("vis_simple", "Видимость", "км"),
    CloudgramRow("hazard_simple", "Опасность", ""),
)

HAZARD_COLORS = ("#E8F5E9", "#CDECCB", "#F5D76E", "#F59E0B", "#991B1B")
VIS_COLORS = ("#7F1D1D", "#EA580C", "#D6A800", "#DCEEFF", "#FFFFFF")
VIS_BOUNDS = (1.0, 3.0, 5.0, 10.0)


def _binned_color(value: float, bounds: tuple[float, ...], colors: tuple[str, ...]) -> str:
    for idx, bound in enumerate(bounds):
        if value < bound:
            return colors[idx]
    return colors[-1]


def _cloud_value(cell: CloudgramCell, key: str) -> float | None:
    return {
        "high": cell.high_cloud_pct,
        "mid": cell.mid_cloud_pct,
        "low": cell.low_cloud_pct,
        "total": cell.total_cloud_pct,
    }.get(key)


def _cloud_text(value: float | None) -> str:
    if value is None:
        return "—"
    return "" if value < 5 else f"{value:.0f}"


def _precip_text(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 0.05:
        return ""
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


def _ceiling_text(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 20000:
        return ">20к"
    if value >= 10000:
        return ">10к"
    if value >= 1000:
        return f"{value / 1000:.1f}к"
    return f"{value:.0f}"


def _vis_text(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 10:
        return ">10"
    if value >= 3:
        return f"{value:.0f}"
    return f"{value:.1f}"


def _pro_cell(row: CloudgramRow, cell: CloudgramCell):
    if row.key in {"high", "mid", "low", "total"}:
        value = _cloud_value(cell, row.key)
        cmap, norm = cloud_cover_cmap_and_norm()
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return cmap(norm(value)), _cloud_text(value), value_text_color(value, param="cloud")

    if row.key == "precip":
        value = cell.precip_mm
        cmap, norm = precip_cmap_and_norm()
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return cmap(norm(value)), _precip_text(value), value_text_color(value, param="precip")

    if row.key == "ptype":
        text = cell.precip_type or "—"
        return PRECIP_TYPE_COLORS.get(text.split("/", 1)[0], "#F3F4F6"), text, METEO.axis_text

    if row.key == "phen":
        text = cell.phenomena or "—"
        return PRECIP_TYPE_COLORS.get(text, "#F8FAFC"), text, METEO.axis_text

    if row.key == "vis":
        value = cell.visibility_km
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return _binned_color(value, VIS_BOUNDS, VIS_COLORS), _vis_text(value), value_text_color(value, param="visibility")

    if row.key == "ceiling":
        value = cell.ceiling_m
        cmap, norm = ceiling_cmap_and_norm()
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return cmap(norm(value)), _ceiling_text(value), value_text_color(value, param="ceiling")

    if row.key == "cb":
        value = float(cell.cb_score)
        cmap, norm = cb_cmap_and_norm()
        return cmap(norm(value)), str(int(value)), value_text_color(value, param="cb")

    if row.key == "hazard":
        value = max(0, min(int(cell.hazard_score), 4))
        color = HAZARD_COLORS[value]
        return color, str(value), "#FFFFFF" if value >= 4 else METEO.axis_text

    return "#FFFFFF", "", METEO.axis_text


def _simple_cloud(cell: CloudgramCell) -> tuple[str, str, str]:
    value = cell.total_cloud_pct
    if value is None:
        return "#E6EBF1", "?", METEO.axis_text
    if value < 20:
        return "#FFF8D6", "☀️", METEO.axis_text
    if value < 50:
        return "#EAF6FF", "🌤️", METEO.axis_text
    if value < 80:
        return "#D8E8F4", "⛅", METEO.axis_text
    return "#9FB3C8", "☁️☁️☁️", "#FFFFFF"


def _simple_precip(cell: CloudgramCell) -> tuple[str, str, str]:
    value = cell.precip_mm or 0.0
    if value < 0.2:
        return "#F8FAFC", "—", METEO.axis_text
    if value < 1:
        return "#DFF6DD", "🌦️", METEO.axis_text
    if value < 5:
        return "#A7E3A3", "🌧️", METEO.axis_text
    return "#2E8B57", "🌧️🌧️", "#FFFFFF"


def _simple_storm(cell: CloudgramCell) -> tuple[str, str, str]:
    score = int(cell.cb_score)
    if score <= 0:
        return "#F8FAFC", "—", METEO.axis_text
    if score == 1:
        return "#FFF3BF", "⚡", METEO.axis_text
    if score == 2:
        return "#FDBA74", "⚡⚡", METEO.axis_text
    return "#DC2626", "⛈️", "#FFFFFF"


def _simple_phen(cell: CloudgramCell) -> tuple[str, str, str]:
    code = cell.phenomena or "—"
    mapping = {
        "—": ("#F8FAFC", "—", METEO.axis_text),
        "RA": ("#DFF6DD", "🌧️", METEO.axis_text),
        "SN": ("#E6F4FF", "🌨️", METEO.axis_text),
        "FZRA": ("#FDE2E2", "🧊🌧️", METEO.axis_text),
        "FG": ("#E5E7EB", "🌫️", METEO.axis_text),
        "TSRA": ("#FEE2E2", "⛈️", METEO.axis_text),
    }
    return mapping.get(code, ("#F8FAFC", code, METEO.axis_text))


def _simple_visibility(cell: CloudgramCell) -> tuple[str, str, str]:
    value = cell.visibility_km
    if value is None:
        return "#E6EBF1", "—", METEO.axis_text
    text = _vis_text(value)
    return _binned_color(value, VIS_BOUNDS, VIS_COLORS), text, "#FFFFFF" if value < 3 else METEO.axis_text


def _simple_hazard(cell: CloudgramCell) -> tuple[str, str, str]:
    value = max(0, min(int(cell.hazard_score), 4))
    text = ("✅", "🟡", "🟠", "🔴", "⛔")[value]
    return HAZARD_COLORS[value], text, "#FFFFFF" if value >= 4 else METEO.axis_text


def _simple_cell(row: CloudgramRow, cell: CloudgramCell):
    if row.key == "cloud_simple":
        return _simple_cloud(cell)
    if row.key == "precip_simple":
        return _simple_precip(cell)
    if row.key == "storm_simple":
        return _simple_storm(cell)
    if row.key == "phen_simple":
        return _simple_phen(cell)
    if row.key == "vis_simple":
        return _simple_visibility(cell)
    if row.key == "hazard_simple":
        return _simple_hazard(cell)
    return "#FFFFFF", "", METEO.axis_text


def _xlabels(data: CloudgramData, *, sparse: bool = False) -> list[str]:
    labels: list[str] = []
    for idx, cell in enumerate(data.cells):
        if sparse and idx % 2 == 1:
            labels.append(f"\n+{cell.lead_hour}")
        else:
            labels.append(f"{cell.valid_time_utc:%d.%m %HZ}\n+{cell.lead_hour}")
    return labels


def _draw_day_separators(ax, data: CloudgramData) -> None:
    previous_day = None
    for index, cell in enumerate(data.cells):
        day = cell.valid_time_utc.date()
        if previous_day is not None and day != previous_day:
            ax.axvline(index - 0.5, color="#53687D", linewidth=1.35, alpha=0.85)
        previous_day = day


def _pro_footer(data: CloudgramData) -> str:
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    missing = f" Нет полей: {', '.join(data.missing_fields)}." if data.missing_fields else ""
    return (
        "Гроза 0–3: 0 нет, 1 слабая, 2 развитая, 3 выраженная. "
        "Опасность 0–4: 0 спокойно, 4 максимум риска. "
        "RA дождь, SN снег, FZRA переохл. дождь, FG туман, TSRA гроза с дождём. "
        f"Макс. опасность: {max_hazard}.{missing}"
    )


def _simple_footer(data: CloudgramData) -> str:
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    return (
        "☀️ ясно · 🌤️ мало облаков · ⛅ облачно · ☁️ пасмурно. "
        "🌦️ слабые осадки · 🌧️ осадки · ⚡ возможна гроза · ⛈️ гроза. "
        "🌫️ туман · 🌨️ снег · 🧊🌧️ ледяной дождь. "
        f"Опасность: ✅ спокойно … ⛔ опасно. Макс: {max_hazard}."
    )


def _write_grid(data: CloudgramData, rows: tuple[CloudgramRow, ...], cell_func, *, title: str, footer: str, simple: bool) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    apply_meteo_rcparams(plt)
    n_cols = len(data.cells)
    n_rows = len(rows)
    fig_width = max(12.0, n_cols * (0.72 if simple else 0.68))
    fig_height = 5.8 if simple else 8.6

    tmp = tempfile.NamedTemporaryFile(prefix="gfs_cloudgram", suffix=".png", delete=False)
    out_path = Path(tmp.name)
    tmp.close()
    fig = None
    try:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height), facecolor=METEO.figure_bg)
        ax.set_facecolor(METEO.axes_bg)
        for y, row in enumerate(rows):
            for x, cell in enumerate(data.cells):
                facecolor, text, text_color = cell_func(row, cell)
                ax.add_patch(Rectangle((x - 0.5, y - 0.5), 1, 1, facecolor=facecolor, edgecolor="#FFFFFF", linewidth=0.9))
                if text:
                    ax.text(x, y, text, ha="center", va="center", fontsize=10 if simple else 7.1, color=text_color, fontweight="bold")

        _draw_day_separators(ax, data)
        if not simple:
            for y in (3.5, 6.5, 8.5):
                ax.axhline(y, color="#53687D", linewidth=1.05, alpha=0.72)
        ax.set_xlim(-0.5, n_cols - 0.5)
        ax.set_ylim(n_rows - 0.5, -0.5)
        ax.set_xticks(range(n_cols))
        ax.set_xticklabels(_xlabels(data, sparse=simple and n_cols > 30), rotation=0, fontsize=7)
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels([f"{row.label}\n{row.unit}" if row.unit else row.label for row in rows], fontsize=8)
        ax.set_xlabel("Дата и время UTC; ниже — заблаговременность прогноза")
        ax.set_ylabel("Параметр")
        ax.set_title(title, fontsize=10.5, fontweight="bold", color=METEO.axis_text, pad=12)
        style_axis(ax, grid=False)
        ax.tick_params(which="both", length=0)
        add_footer(fig, footer, y=0.012)
        fig.tight_layout(rect=(0, 0.10 if simple else 0.08, 1, 1))
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
        f"GFS 0.25 · cloudgram PRO: облачность, осадки, явления, ВНГО и опасность · {data.run.date} {data.run.cycle}Z · "
        f"+{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}"
    )
    return _write_grid(data, PRO_ROWS, _pro_cell, title=title, footer=_pro_footer(data), simple=False)


def _write_cloudgram_simple_png(data: CloudgramData) -> Path:
    title = (
        f"GFS 0.25 · cloudgram SIMPLE: облака, осадки, гроза, видимость и опасность · {data.run.date} {data.run.cycle}Z · "
        f"+{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}"
    )
    return _write_grid(data, SIMPLE_ROWS, _simple_cell, title=title, footer=_simple_footer(data), simple=True)


def write_cloudgram_png(data: CloudgramData, mode: str = "pro") -> Path:
    if mode == "simple":
        return _write_cloudgram_simple_png(data)
    return _write_cloudgram_pro_png(data)
