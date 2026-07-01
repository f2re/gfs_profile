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


PRO_ROWS = (
    CloudgramRow("cloud_layers", "Облачность", "H/M/L + общ.%"),
    CloudgramRow("precip", "Осадки", "мм"),
    CloudgramRow("phen", "Явления", ""),
    CloudgramRow("vis", "Видимость", "км"),
    CloudgramRow("ceiling", "ВНГО", "м"),
    CloudgramRow("cb", "Грозовой\nриск", "0–3"),
    CloudgramRow("hazard", "Опасность", "0–4"),
)

SIMPLE_ROWS = (
    CloudgramRow("cloud_simple", "Облака", "верх/сред/низ"),
    CloudgramRow("wx_simple", "Осадки /\nявления", ""),
    CloudgramRow("storm_simple", "Гроза", ""),
    CloudgramRow("vis_simple", "Видимость", "км"),
    CloudgramRow("hazard_simple", "Опасность", "0–4"),
)

HAZARD_COLORS = ("#E8F5E9", "#CDECCB", "#F5D76E", "#F59E0B", "#991B1B")
VIS_COLORS = ("#7F1D1D", "#EA580C", "#D6A800", "#DCEEFF", "#FFFFFF")
VIS_BOUNDS = (1.0, 3.0, 5.0, 10.0)


def _binned_color(value: float, bounds: tuple[float, ...], colors: tuple[str, ...]) -> str:
    for idx, bound in enumerate(bounds):
        if value < bound:
            return colors[idx]
    return colors[-1]


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


def _cloud_band_color(value: float | None):
    if value is None:
        return "#E6EBF1"
    if value < 5:
        return "#FFFFFF"
    cmap, norm = cloud_cover_cmap_and_norm()
    return cmap(norm(value))


def _icon_y_scale(ax) -> float:
    return float(getattr(ax, "_gfs_icon_y_scale", 1.0))


def _iy(ax, y: float, dy: float) -> float:
    return y + dy * _icon_y_scale(ax)


def _ih(ax, height: float) -> float:
    return height * _icon_y_scale(ax)


def _set_icon_y_scale(ax, *, fig_width: float, fig_height: float, n_cols: int, n_rows: int) -> None:
    x_span = max(float(n_cols), 1.0)
    y_span = float(n_rows) + 1.08
    scale = (fig_width * y_span) / max(fig_height * x_span, 0.001)
    ax._gfs_icon_y_scale = max(0.38, min(1.15, scale))


def _draw_sun_icon(ax, x: float, y: float, *, scale: float = 0.18) -> None:
    from matplotlib.patches import Ellipse

    radius = scale * 0.62
    ax.add_patch(Ellipse((x, y), radius * 2.0, _ih(ax, radius * 2.0), facecolor="#FBBF24", edgecolor="#B45309", linewidth=0.55, zorder=6))
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7)):
        ax.plot(
            [x + dx * scale * 0.82, x + dx * scale * 1.16],
            [_iy(ax, y, dy * scale * 0.82), _iy(ax, y, dy * scale * 1.16)],
            color="#D97706",
            linewidth=0.75,
            solid_capstyle="round",
            zorder=6,
        )


def _draw_cloud_icon(ax, x: float, y: float, *, scale: float = 0.22, color: str = "#D1D9E6", edge: str = "#66788A") -> None:
    from matplotlib.patches import Ellipse, Rectangle

    ax.add_patch(Ellipse((x - scale * 0.42, _iy(ax, y, scale * 0.05)), scale * 0.72, _ih(ax, scale * 0.56), facecolor=color, edgecolor=edge, linewidth=0.55, zorder=7))
    ax.add_patch(Ellipse((x, _iy(ax, y, -scale * 0.09)), scale * 0.86, _ih(ax, scale * 0.72), facecolor=color, edgecolor=edge, linewidth=0.55, zorder=7))
    ax.add_patch(Ellipse((x + scale * 0.45, _iy(ax, y, scale * 0.07)), scale * 0.72, _ih(ax, scale * 0.54), facecolor=color, edgecolor=edge, linewidth=0.55, zorder=7))
    ax.add_patch(Rectangle((x - scale * 0.70, _iy(ax, y, scale * 0.02)), scale * 1.40, _ih(ax, scale * 0.38), facecolor=color, edgecolor="none", zorder=7))


def _draw_raindrops(ax, x: float, y: float, *, scale: float = 0.18, count: int = 3) -> None:
    xs = [x - scale * 0.58, x, x + scale * 0.58] if count >= 3 else [x - scale * 0.28, x + scale * 0.28]
    for px in xs[:count]:
        ax.plot([px, px - scale * 0.16], [_iy(ax, y, -scale * 0.02), _iy(ax, y, scale * 0.42)], color="#0EA5E9", linewidth=1.15, zorder=8, solid_capstyle="round")


def _draw_lightning_icon(ax, x: float, y: float, *, scale: float = 0.22) -> None:
    from matplotlib.patches import Polygon

    raw = [
        (-0.12, -0.55),
        (0.24, -0.55),
        (0.02, -0.08),
        (0.30, -0.08),
        (-0.17, 0.62),
        (-0.02, 0.12),
        (-0.30, 0.12),
    ]
    pts = [(x + dx * scale, _iy(ax, y, dy * scale)) for dx, dy in raw]
    ax.add_patch(Polygon(pts, closed=True, facecolor="#FACC15", edgecolor="#B45309", linewidth=0.55, zorder=9))


def _draw_fog_icon(ax, x: float, y: float, *, scale: float = 0.23) -> None:
    for offset in (-0.22, 0.0, 0.22):
        ax.plot([x - scale * 1.1, x + scale * 1.1], [_iy(ax, y, offset * scale * 2.1), _iy(ax, y, offset * scale * 2.1)], color="#6B7280", linewidth=1.0, alpha=0.9, zorder=8, solid_capstyle="round")


def _draw_snow_icon(ax, x: float, y: float, *, scale: float = 0.19) -> None:
    for angle in (0, 60, 120):
        dx = math.cos(math.radians(angle)) * scale
        dy = math.sin(math.radians(angle)) * scale
        ax.plot([x - dx, x + dx], [_iy(ax, y, -dy), _iy(ax, y, dy)], color="#2563EB", linewidth=0.85, zorder=8, solid_capstyle="round")


def _draw_ice_icon(ax, x: float, y: float, *, scale: float = 0.17) -> None:
    from matplotlib.patches import Polygon

    pts = [(x, _iy(ax, y, -scale)), (x + scale * 0.74, y), (x, _iy(ax, y, scale)), (x - scale * 0.74, y)]
    ax.add_patch(Polygon(pts, closed=True, facecolor="#BFDBFE", edgecolor="#2563EB", linewidth=0.65, zorder=8))


def _draw_hazard_icon(ax, x: float, y: float, level: int) -> None:
    from matplotlib.patches import Ellipse, Polygon

    level = max(0, min(level, 4))
    if level <= 2:
        ax.add_patch(Ellipse((x, y), 0.32, _ih(ax, 0.32), facecolor="#FFFFFF", edgecolor="#53687D", linewidth=0.6, zorder=8))
    else:
        r = 0.20
        pts = [(x + r * math.cos(math.radians(60 * idx + 30)), _iy(ax, y, r * math.sin(math.radians(60 * idx + 30)))) for idx in range(6)]
        ax.add_patch(Polygon(pts, closed=True, facecolor="#7F1D1D" if level == 4 else "#DC2626", edgecolor="#FFFFFF", linewidth=0.7, zorder=8))
    ax.text(x, y, str(level), ha="center", va="center", fontsize=7.0, color="#FFFFFF" if level >= 3 else METEO.axis_text, fontweight="bold", zorder=9)


def _draw_icon(ax, kind: str, x: float, y: float) -> None:
    if kind == "clear":
        _draw_sun_icon(ax, x, y, scale=0.15)
    elif kind == "partly":
        _draw_sun_icon(ax, x - 0.10, _iy(ax, y, -0.04), scale=0.12)
        _draw_cloud_icon(ax, x + 0.08, _iy(ax, y, 0.06), scale=0.16)
    elif kind == "cloud":
        _draw_cloud_icon(ax, x, y, scale=0.20)
    elif kind == "overcast":
        _draw_cloud_icon(ax, x - 0.08, y, scale=0.19, color="#AEBCCE")
        _draw_cloud_icon(ax, x + 0.12, _iy(ax, y, 0.03), scale=0.17, color="#8FA1B6")
    elif kind == "rain_light":
        _draw_cloud_icon(ax, x, _iy(ax, y, -0.05), scale=0.18)
        _draw_raindrops(ax, x, _iy(ax, y, 0.16), scale=0.12, count=2)
    elif kind == "rain":
        _draw_cloud_icon(ax, x, _iy(ax, y, -0.06), scale=0.18)
        _draw_raindrops(ax, x, _iy(ax, y, 0.13), scale=0.14, count=3)
    elif kind == "storm":
        _draw_cloud_icon(ax, x, _iy(ax, y, -0.05), scale=0.18, color="#CBD5E1")
        _draw_lightning_icon(ax, x, _iy(ax, y, 0.12), scale=0.15)
    elif kind == "storm_1":
        _draw_lightning_icon(ax, x, y, scale=0.13)
    elif kind == "storm_2":
        _draw_lightning_icon(ax, x - 0.07, y, scale=0.12)
        _draw_lightning_icon(ax, x + 0.08, y, scale=0.12)
    elif kind == "storm_3":
        _draw_icon(ax, "storm", x, y)
    elif kind == "fog":
        _draw_fog_icon(ax, x, y, scale=0.18)
    elif kind == "snow":
        _draw_cloud_icon(ax, x, _iy(ax, y, -0.05), scale=0.17)
        _draw_snow_icon(ax, x, _iy(ax, y, 0.17), scale=0.10)
    elif kind == "ice_rain":
        _draw_ice_icon(ax, x - 0.08, y, scale=0.10)
        _draw_raindrops(ax, x + 0.12, _iy(ax, y, -0.02), scale=0.11, count=2)
    elif kind.startswith("hazard_"):
        _draw_hazard_icon(ax, x, y, int(kind.rsplit("_", 1)[1]))


def _draw_cloud_layers_cell(ax, x: int, y: int, cell: CloudgramCell, rectangle_cls, *, simple: bool = False) -> None:
    layers = (
        ("H", cell.high_cloud_pct),
        ("M", cell.mid_cloud_pct),
        ("L", cell.low_cloud_pct),
    )
    band_h = 1.0 / 3.0
    for idx, (_, value) in enumerate(layers):
        y0 = y - 0.5 + idx * band_h
        ax.add_patch(
            rectangle_cls(
                (x - 0.5, y0),
                1.0,
                band_h,
                facecolor=_cloud_band_color(value),
                edgecolor="#FFFFFF",
                linewidth=0.45,
            )
        )
    ax.add_patch(rectangle_cls((x - 0.5, y - 0.5), 1.0, 1.0, facecolor="none", edgecolor="#B7C6D8", linewidth=0.75))

    text = _cloud_text(cell.total_cloud_pct)
    if simple:
        total = cell.total_cloud_pct
        kind = "cloud" if total is None else ("clear" if total < 20 else "partly" if total < 50 else "cloud" if total < 80 else "overcast")
        _draw_icon(ax, kind, x, _iy(ax, y, -0.02))
        return

    if text:
        ax.text(
            x,
            y,
            text,
            ha="center",
            va="center",
            fontsize=7.0,
            color=METEO.axis_text,
            fontweight="bold",
            bbox={"boxstyle": "round,pad=0.10", "facecolor": "#FFFFFF", "alpha": 0.78, "edgecolor": "none"},
        )


def _pro_cell(row: CloudgramRow, cell: CloudgramCell):
    if row.key == "precip":
        value = cell.precip_mm
        cmap, norm = precip_cmap_and_norm()
        if value is None:
            return "#E6EBF1", "—", METEO.axis_text
        return cmap(norm(value)), _precip_text(value), value_text_color(value, param="precip")

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


def _precip_bg(value: float) -> str:
    if value < 0.2:
        return "#F8FAFC"
    if value < 1.0:
        return "#DFF6DD"
    if value < 5.0:
        return "#A7E3A3"
    return "#2E8B57"


def _simple_wx(cell: CloudgramCell) -> tuple[str, str, str]:
    precip = cell.precip_mm or 0.0
    code = cell.phenomena or "—"
    if "TS" in code:
        return "#FEE2E2", "icon:storm", METEO.axis_text
    if "FZ" in code:
        return "#FDE2E2", "icon:ice_rain", METEO.axis_text
    if "SN" in code or "S" == code:
        return "#E6F4FF", "icon:snow", METEO.axis_text
    if "FG" in code:
        return "#E5E7EB", "icon:fog", METEO.axis_text
    if precip >= 0.2 or "RA" in code or "R" == code:
        return _precip_bg(precip), "icon:rain", "#FFFFFF" if precip >= 5.0 else METEO.axis_text
    return "#F8FAFC", "", METEO.axis_text


def _simple_storm(cell: CloudgramCell) -> tuple[str, str, str]:
    score = int(cell.cb_score)
    precip = cell.precip_mm or 0.0
    conv_precip = cell.conv_precip_mm or 0.0
    if cell.phenomena == "TSRA":
        return "#DC2626", "icon:storm_3", "#FFFFFF"
    if score < 2:
        return "#F8FAFC", "", METEO.axis_text
    if precip < 0.1 and conv_precip < 0.1:
        return "#F8FAFC", "", METEO.axis_text
    if score == 2:
        return "#FDBA74", "icon:storm_1", METEO.axis_text
    return "#DC2626", "icon:storm_3", "#FFFFFF"


def _simple_visibility(cell: CloudgramCell) -> tuple[str, str, str]:
    value = cell.visibility_km
    if value is None:
        return "#E6EBF1", "—", METEO.axis_text
    return _binned_color(value, VIS_BOUNDS, VIS_COLORS), _vis_text(value), "#FFFFFF" if value < 3 else METEO.axis_text


def _simple_hazard(cell: CloudgramCell) -> tuple[str, str, str]:
    value = max(0, min(int(cell.hazard_score), 4))
    return HAZARD_COLORS[value], f"icon:hazard_{value}", "#FFFFFF" if value >= 4 else METEO.axis_text


def _simple_cell(row: CloudgramRow, cell: CloudgramCell):
    if row.key == "wx_simple":
        return _simple_wx(cell)
    if row.key == "storm_simple":
        return _simple_storm(cell)
    if row.key == "vis_simple":
        return _simple_visibility(cell)
    if row.key == "hazard_simple":
        return _simple_hazard(cell)
    return "#FFFFFF", "", METEO.axis_text


def _hour_lead_labels(data: CloudgramData, *, sparse: bool = False) -> list[str]:
    labels: list[str] = []
    for idx, cell in enumerate(data.cells):
        if sparse and idx % 2 == 1:
            labels.append(f"\n+{cell.lead_hour}")
        else:
            labels.append(f"{cell.valid_time_utc:%HZ}\n+{cell.lead_hour}")
    return labels


def _draw_day_separators_and_labels(ax, data: CloudgramData, y_date: float) -> None:
    if not data.cells:
        return
    start = 0
    current_day = data.cells[0].valid_time_utc.date()
    for idx, cell in enumerate(data.cells[1:], start=1):
        day = cell.valid_time_utc.date()
        if day != current_day:
            ax.axvline(idx - 0.5, color="#53687D", linewidth=1.35, alpha=0.85)
            center = (start + idx - 1) / 2.0
            ax.text(center, y_date, data.cells[start].valid_time_utc.strftime("%d.%m"), ha="center", va="center", fontsize=8, color=METEO.axis_text, fontweight="bold")
            start = idx
            current_day = day
    center = (start + len(data.cells) - 1) / 2.0
    ax.text(center, y_date, data.cells[start].valid_time_utc.strftime("%d.%m"), ha="center", va="center", fontsize=8, color=METEO.axis_text, fontweight="bold")


def _pro_footer(data: CloudgramData) -> str:
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    missing = f" Нет полей: {', '.join(data.missing_fields)}." if data.missing_fields else ""
    return (
        "Облачность: одна ячейка = H/M/L сверху вниз, число = общая облачность %. "
        "Гроза 0–3: 0 нет, 1 слабая, 2 развитая, 3 выраженная. "
        "Опасность 0–4: 0 спокойно, 4 максимум риска. "
        "RA дождь, SN снег, FZRA переохл. дождь, FG туман, TSRA гроза с дождём. "
        f"Макс. опасность: {max_hazard}.{missing}"
    )


def _simple_footer(data: CloudgramData) -> str:
    max_hazard = max((cell.hazard_score for cell in data.cells), default=0)
    return (
        "Облака: верх/сред/низ сверху вниз; центр = общая облачность. "
        "Осадки/явления: фон = интенсивность, значок = тип; гроза — только при сигнале.\n"
        "Опасность = максимум сигналов: осадки, видимость, ВНГО, гроза. "
        f"0 спокойно · 1 слабые · 2 ограничения · 3 опасно · 4 гроза/очень опасно. Макс: {max_hazard}."
    )


def _write_grid(data: CloudgramData, rows: tuple[CloudgramRow, ...], cell_func, *, title: str, footer: str, simple: bool) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    apply_meteo_rcparams(plt)
    n_cols = len(data.cells)
    n_rows = len(rows)
    fig_width = max(10.0, n_cols * (0.54 if not simple else 0.42))
    fig_height = 4.70 if simple else 6.7

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

        y_date = n_rows + 0.22
        _draw_day_separators_and_labels(ax, data, y_date)
        if not simple:
            for y in (0.5, 2.5, 4.5):
                ax.axhline(y, color="#53687D", linewidth=1.05, alpha=0.72)
        ax.set_xlim(-0.5, n_cols - 0.5)
        ax.set_ylim(n_rows + 0.58, -0.5)
        ax.set_xticks(range(n_cols))
        ax.set_xticklabels(_hour_lead_labels(data, sparse=n_cols > 60), rotation=0, fontsize=7)
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels([f"{row.label}\n{row.unit}" if row.unit else row.label for row in rows], fontsize=8)
        ax.set_xlabel("UTC-время; ниже — заблаговременность +ч")
        ax.set_ylabel("Параметр")
        ax.set_title(title, fontsize=10.5, fontweight="bold", color=METEO.axis_text, pad=12)
        style_axis(ax, grid=False)
        ax.tick_params(which="both", length=0)
        if simple:
            fig.text(0.5, 0.030, footer, ha="center", va="bottom", fontsize=6.4, color=METEO.muted_text, linespacing=1.08)
        else:
            add_footer(fig, footer, y=0.012)
        fig.tight_layout(rect=(0, 0.125 if simple else 0.10, 1, 1))
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
        f"GFS 0.25 · cloudgram PRO: облачность H/M/L, осадки, явления, ВНГО и опасность · {data.run.date} {data.run.cycle}Z · "
        f"+{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}"
    )
    return _write_grid(data, PRO_ROWS, _pro_cell, title=title, footer=_pro_footer(data), simple=False)


def _write_cloudgram_simple_png(data: CloudgramData) -> Path:
    title = (
        f"GFS 0.25 · cloudgram SIMPLE: облака по ярусам, осадки/явления, гроза, видимость и опасность · {data.run.date} {data.run.cycle}Z · "
        f"+{data.leads[0]}…+{data.leads[-1]} ч · узел {data.grid_lat:.2f}, {data.grid_lon:.2f}"
    )
    return _write_grid(data, SIMPLE_ROWS, _simple_cell, title=title, footer=_simple_footer(data), simple=True)


def write_cloudgram_png(data: CloudgramData, mode: str = "pro") -> Path:
    if mode == "simple":
        return _write_cloudgram_simple_png(data)
    return _write_cloudgram_pro_png(data)
