from __future__ import annotations

"""Compact native PDF rendering for meteogram reports.

The layout is intentionally information-dense: one overview page with the
meteogram, followed by compact tables. Row heights depend on actual content,
so short forecasts do not produce mostly empty pages.
"""

import re
import textwrap
from pathlib import Path
from typing import Any, Sequence


A4_LANDSCAPE = (11.69, 8.27)
PDF_DPI = 144

INK = "#1D2A33"
MUTED = "#65747D"
ACCENT = "#24566C"
GRID = "#CAD5DA"
HEADER_BG = "#E7EEF1"
ROW_ALT = "#F6F9FA"
SUMMARY_BG = "#F2F6F7"
WARNING_BG = "#FFF6E8"


class MeteogramPdfError(RuntimeError):
    pass


def write_meteogram_pdf(
    report_data: Any,
    chart_path: str | Path,
    output_path: str | Path,
) -> Path:
    """Write a compact landscape A4 PDF from ``MeteogramReportData``."""

    chart = Path(chart_path)
    output = Path(output_path)
    if not chart.is_file() or chart.stat().st_size <= 0:
        raise MeteogramPdfError("PNG метеограммы не найден или пуст")

    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.image as mpimg
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
    except Exception as exc:  # pragma: no cover
        raise MeteogramPdfError(f"Не удалось загрузить PDF-рендерер: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)

    try:
        image = _crop_image(mpimg.imread(chart))
        metadata = {
            "Title": str(getattr(report_data, "title", "Метеограмма")),
            "Author": "GFS Profile Bot",
            "Subject": "Модельный метеорологический прогноз",
        }
        with PdfPages(temporary, metadata=metadata) as pdf:
            _overview_page(pdf, plt, report_data, image)
            _detail_pages(pdf, plt, report_data)

        if not temporary.is_file() or temporary.stat().st_size < 1500:
            raise MeteogramPdfError("PDF-рендерер создал пустой или повреждённый файл")
        with temporary.open("rb") as file_obj:
            if file_obj.read(5) != b"%PDF-":
                raise MeteogramPdfError("Сформированный файл не является PDF")
        temporary.replace(output)
        return output
    except MeteogramPdfError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise MeteogramPdfError(f"Не удалось сформировать PDF: {exc}") from exc


def _overview_page(pdf: Any, plt: Any, data: Any, image: Any) -> None:
    from matplotlib.patches import FancyBboxPatch

    fig = _page(plt)
    title = str(data.title)
    subtitle = str(data.subtitle)

    fig.text(0.045, 0.955, title, fontsize=15.2, fontweight="bold", color=INK, va="top")
    fig.text(0.045, 0.915, subtitle, fontsize=10.2, fontweight="bold", color=ACCENT, va="top")

    meta = "  •  ".join(item for item in (str(data.period_line), str(data.source_line)) if item)
    fig.text(0.045, 0.884, _wrap_text(meta, 150), fontsize=7.4, color=MUTED, va="top", linespacing=1.15)
    fig.text(0.045, 0.848, _wrap_text(str(data.point_line), 150), fontsize=7.4, color=MUTED, va="top")

    lines = list(getattr(data, "main_lines", ()) or ())[:5]
    panel_top = 0.808
    panel_height = _summary_height(lines)
    panel_bottom = panel_top - panel_height
    panel = FancyBboxPatch(
        (0.042, panel_bottom), 0.916, panel_height,
        boxstyle="round,pad=0.004,rounding_size=0.005",
        linewidth=0.55, edgecolor=GRID, facecolor=SUMMARY_BG,
        transform=fig.transFigure, clip_on=False,
    )
    fig.add_artist(panel)
    fig.text(0.055, panel_top - 0.016, "Кратко", fontsize=9.0, fontweight="bold", color=INK, va="top")
    _draw_summary_lines(fig, lines, panel_top - 0.047, panel_bottom + 0.012)

    chart_bottom = 0.066
    chart_top = panel_bottom - 0.020
    fig.text(0.045, chart_top + 0.006, "Метеограмма", fontsize=8.8, fontweight="bold", color=INK, va="bottom")
    chart_ax = fig.add_axes([0.045, chart_bottom, 0.91, max(0.28, chart_top - chart_bottom)])
    chart_ax.imshow(image)
    chart_ax.axis("off")

    _footer(fig)
    pdf.savefig(fig)
    plt.close(fig)

def _detail_pages(pdf: Any, plt: Any, data: Any) -> None:
    ensemble = _is_ensemble(data)
    daily = _daily_spec(data, ensemble)
    control = _control_spec(data, ensemble)
    daily_rows = list(daily[1])
    control_rows = list(control[1])
    notes_drawn = False
    page_index = 0

    while daily_rows or control_rows:
        page_index += 1
        fig = _page(plt)
        heading = "Прогноз в деталях" if page_index == 1 else "Прогноз в деталях · продолжение"
        fig.text(0.045, 0.955, heading, fontsize=13.3, fontweight="bold", color=INK, va="top")
        y = 0.905

        if daily_rows:
            available = max(0.12, y - 0.075 - 0.035)
            batch, daily_rows = _take_rows(daily_rows, daily[2], available)
            y = _draw_table(fig, plt, y, "По суткам", daily[0], batch, daily[2], daily[3], daily[4])
            if daily_rows:
                _footer(fig)
                pdf.savefig(fig)
                plt.close(fig)
                continue

        if control_rows:
            available = max(0.12, y - 0.075 - 0.035)
            batch, remaining = _take_rows(control_rows, control[2], available)
            if batch:
                y = _draw_table(fig, plt, y, "По срокам", control[0], batch, control[2], control[3], control[4])
                control_rows = remaining

        if not daily_rows and not control_rows:
            need = _notes_height(data)
            if need <= max(0.0, y - 0.050):
                _draw_notes(fig, y - 0.002, data)
                notes_drawn = True

        _footer(fig)
        pdf.savefig(fig)
        plt.close(fig)

    if not notes_drawn and _notes_height(data) > 0:
        fig = _page(plt)
        fig.text(0.045, 0.955, "Примечания", fontsize=11.0, fontweight="bold", color=INK, va="top")
        _draw_notes(fig, 0.900, data)
        _footer(fig)
        pdf.savefig(fig)
        plt.close(fig)


def _take_rows(
    rows: Sequence[Sequence[str]],
    wrap_widths: Sequence[int],
    max_height: float,
) -> tuple[list[Sequence[str]], list[Sequence[str]]]:
    batch: list[Sequence[str]] = []
    for row in rows:
        candidate = batch + [row]
        if batch and _table_height(candidate, wrap_widths) > max_height:
            break
        batch = candidate
        if _table_height(batch, wrap_widths) >= max_height:
            break
    if not batch and rows:
        batch = [rows[0]]
    return batch, list(rows[len(batch) :])

def _daily_spec(data: Any, ensemble: bool):
    rows = list(getattr(data, "daily_rows", ()) or ())
    show_members = ensemble and not _uniform_member_count(rows)
    if show_members:
        headers = ("Дата", "Погода", "Температура", "Осадки", "Ветер", "Давление", "Ансамбль")
        wrap = (9, 20, 20, 31, 22, 15, 14)
        widths = (0.07, 0.14, 0.15, 0.22, 0.17, 0.11, 0.14)
    else:
        headers = ("Дата", "Погода", "Температура", "Осадки", "Ветер", "Давление")
        wrap = (9, 23, 22, 36, 25, 17)
        widths = (0.07, 0.17, 0.16, 0.24, 0.19, 0.17)
    values = []
    for row in rows:
        base = [
            f"{row.day:%d.%m}\n{_weekday_ru(row.day.weekday())}",
            _pretty_cell(row.weather),
            _pretty_cell(row.temperature),
            _compact_precipitation(row.precipitation, ensemble=ensemble, control=False),
            _pretty_cell(row.wind),
            _pretty_cell(row.pressure),
        ]
        if show_members:
            base.append(_member_count_label(row.ensemble))
        values.append(base)
    return headers, values, wrap, widths, 7.0

def _control_spec(data: Any, ensemble: bool):
    rows = list(getattr(data, "control_rows", ()) or ())
    show_members = ensemble and not _uniform_member_count(rows)
    if show_members:
        headers = ("Срок", "T / Td", "RH / облака", "Осадки", "Ветер", "Давление", "Ансамбль")
        wrap = (10, 19, 23, 31, 25, 16, 14)
        widths = (0.08, 0.13, 0.16, 0.22, 0.17, 0.11, 0.13)
    else:
        headers = ("Срок", "T / Td", "RH / облака", "Осадки", "Ветер", "Давление")
        wrap = (10, 21, 25, 36, 29, 17)
        widths = (0.09, 0.15, 0.18, 0.24, 0.20, 0.14)
    values = []
    for row in rows:
        base = [
            f"{row.time:%d.%m}\n{row.time:%H:%M}",
            _pretty_cell(row.temperature),
            _pretty_cell(row.humidity_cloud),
            _compact_precipitation(row.precipitation, ensemble=ensemble, control=True),
            _pretty_cell(row.wind),
            _pretty_cell(row.pressure),
        ]
        if show_members:
            base.append(_member_count_label(row.ensemble))
        values.append(base)
    return headers, values, wrap, widths, 6.8

def _draw_table(
    fig: Any,
    plt: Any,
    top: float,
    title: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    wrap_widths: Sequence[int],
    column_widths: Sequence[float],
    font_size: float,
) -> float:
    if not rows:
        return top
    if title:
        fig.text(0.045, top, title, fontsize=8.9, fontweight="bold", color=INK, va="top")
        top -= 0.030

    wrapped = [[_wrap_cell(value, width) for value, width in zip(row, wrap_widths)] for row in rows]
    row_heights = _row_heights(wrapped)
    height = 0.036 + sum(row_heights)
    bottom = top - height
    ax = fig.add_axes([0.045, bottom, 0.91, height])
    ax.axis("off")
    table = ax.table(
        cellText=wrapped,
        colLabels=list(headers),
        colWidths=list(column_widths),
        cellLoc="left",
        colLoc="left",
        loc="upper left",
        bbox=[0.0, 0.0, 1.0, 1.0],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    total_height = 0.036 + sum(row_heights)
    for (row_index, col_index), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_linewidth(0.45)
        cell.PAD = 0.025
        if row_index == 0:
            cell.set_height(0.036 / total_height)
            cell.set_facecolor(HEADER_BG)
            cell.set_text_props(weight="bold", ha="left", va="center", fontsize=font_size + 0.25, color=INK)
        else:
            cell.set_height(row_heights[row_index - 1] / total_height)
            if row_index % 2 == 0:
                cell.set_facecolor(ROW_ALT)
            cell.set_text_props(ha="left", va="center", fontsize=font_size, color=INK)
            if col_index == 0:
                cell.set_text_props(ha="center", va="center", fontsize=font_size, color=INK)
    return bottom - 0.018


def _draw_notes(fig: Any, top: float, data: Any) -> float:
    methods = list(getattr(data, "method_lines", ()) or ())[:6]
    warnings = list(getattr(data, "warning_lines", ()) or ())[:4]
    if not methods and not warnings:
        return top
    if top < 0.075:
        return top

    fig.text(0.045, top, "Примечания", fontsize=8.0, fontweight="bold", color=INK, va="top")
    y = top - 0.024
    if methods:
        method_text = " • ".join(str(line).rstrip(".") for line in methods) + "."
        wrapped = _wrap_text(method_text, 148)
        fig.text(0.055, y, wrapped, fontsize=6.9, color=MUTED, va="top", linespacing=1.12)
        y -= 0.0175 * (wrapped.count("\n") + 1) + 0.006

    if warnings and y > 0.060:
        warning_text = " • ".join(str(line).rstrip(".") for line in warnings) + "."
        wrapped = _wrap_text(warning_text, 148)
        fig.text(0.055, y, wrapped, fontsize=6.8, color="#765527", va="top", linespacing=1.12)
        y -= 0.0175 * (wrapped.count("\n") + 1)
    return y

def _draw_summary_lines(fig: Any, lines: Sequence[str], top: float, bottom: float) -> None:
    if not lines:
        fig.text(0.055, top, "Существенных особенностей не выделено.", fontsize=7.7, color=INK, va="top")
        return

    split = (len(lines) + 1) // 2
    columns = (lines[:split], lines[split:])
    for col_index, column in enumerate(columns):
        if not column:
            continue
        x = 0.055 if col_index == 0 else 0.515
        y = top
        width = 64
        for line in column:
            label, value = _summary_parts(str(line))
            fig.text(x, y, label, fontsize=6.7, fontweight="bold", color=ACCENT, va="top")
            wrapped = _wrap_text(value, width)
            fig.text(x, y - 0.016, wrapped, fontsize=7.35, color=INK, va="top", linespacing=1.08)
            y -= 0.019 + 0.018 * max(1, wrapped.count("\n") + 1) + 0.006
            if y < bottom:
                break

def _summary_height(lines: Sequence[str]) -> float:
    if not lines:
        return 0.095
    split = (len(lines) + 1) // 2
    columns = (lines[:split], lines[split:])
    heights = []
    for column in columns:
        used = 0.0
        for line in column:
            _label, value = _summary_parts(str(line))
            wrapped_lines = max(1, len(_wrap_text(value, 64).splitlines()))
            used += 0.019 + 0.018 * wrapped_lines + 0.006
        heights.append(used)
    return min(0.185, max(0.105, 0.054 + max(heights or [0.05])))

def _table_height(rows: Sequence[Sequence[str]], wrap_widths: Sequence[int]) -> float:
    wrapped = [[_wrap_cell(value, width) for value, width in zip(row, wrap_widths)] for row in rows]
    return 0.036 + sum(_row_heights(wrapped))


def _row_heights(rows: Sequence[Sequence[str]]) -> list[float]:
    result = []
    for row in rows:
        lines = max((str(cell).count("\n") + 1 for cell in row), default=1)
        # Do not cap multi-line cells so tightly that text can overlap the next row.
        result.append(min(0.095, max(0.034, 0.028 + 0.0115 * max(0, lines - 1))))
    return result

def _paginate_rows(rows: Sequence[Sequence[str]], wrap_widths: Sequence[int], max_height: float) -> list[list[Sequence[str]]]:
    pages: list[list[Sequence[str]]] = []
    current: list[Sequence[str]] = []
    for row in rows:
        candidate = current + [row]
        if current and _table_height(candidate, wrap_widths) > max_height:
            pages.append(current)
            current = [row]
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages


def _notes_height(data: Any) -> float:
    methods = list(getattr(data, "method_lines", ()) or ())[:6]
    warnings = list(getattr(data, "warning_lines", ()) or ())[:4]
    if not methods and not warnings:
        return 0.0
    method_text = " • ".join(str(line).rstrip(".") for line in methods)
    method_lines = len(_wrap_text(method_text, 148).splitlines()) if method_text else 0
    warning_text = " • ".join(str(line).rstrip(".") for line in warnings)
    warning_lines = len(_wrap_text(warning_text, 148).splitlines()) if warning_text else 0
    return 0.031 + method_lines * 0.0175 + (0.006 + warning_lines * 0.0175 if warning_lines else 0.0)

def _is_ensemble(data: Any) -> bool:
    if "ансамб" in str(getattr(data, "title", "")).lower():
        return True
    for attr in ("daily_rows", "control_rows"):
        for row in list(getattr(data, attr, ()) or ()):
            value = str(getattr(row, "ensemble", "")).lower()
            if "/" in value or "член" in value:
                return True
    return False


def _crop_image(image: Any) -> Any:
    try:
        import numpy as np

        array = np.asarray(image)
        rgb = array[..., :3]
        mask = np.any(rgb < 0.985, axis=2)
        ys, xs = np.where(mask)
        if not len(xs) or not len(ys):
            return image
        pad = 10
        y0 = max(0, int(ys.min()) - pad)
        y1 = min(array.shape[0], int(ys.max()) + pad + 1)
        x0 = max(0, int(xs.min()) - pad)
        x1 = min(array.shape[1], int(xs.max()) + pad + 1)
        content = array[y0:y1, x0:x1]
        # Legacy Telegram PNGs contain their own title/footer. For report PDFs,
        # trim those repeated bands conservatively; report-mode PNGs already
        # have little or no extra band and therefore remain almost untouched.
        h = content.shape[0]
        if h >= 500:
            top = int(h * 0.075)
            bottom = int(h * 0.055)
            if h - top - bottom >= int(h * 0.70):
                content = content[top : h - bottom]
        return content
    except Exception:
        return image


def _pretty_cell(value: Any) -> str:
    text = str(value or "—").replace("q10-q90", "q10–q90").replace("q25-q75", "q25–q75")
    return text.replace(">=", "≥")


def _member_count_label(value: Any) -> str:
    text = str(value or "—")
    first = text.splitlines()[0].strip()
    match = re.search(r"\b(\d+\s*/\s*\d+)\b", first)
    return match.group(1).replace(" ", "") if match else first


def _uniform_member_count(rows: Sequence[Any]) -> bool:
    labels = {_member_count_label(getattr(row, "ensemble", "")) for row in rows}
    labels.discard("")
    labels.discard("—")
    return len(labels) <= 1 and bool(labels)


def _compact_precipitation(value: Any, *, ensemble: bool, control: bool) -> str:
    raw_lines = [line.strip() for line in str(value or "—").splitlines() if line.strip()]
    probability_lines: list[tuple[float, float, str]] = []
    body: list[str] = []
    has_spread = any("q10" in line.lower() and "q90" in line.lower() for line in raw_lines)

    for line in raw_lines:
        text = _pretty_cell(line)
        lower = text.lower()
        if "сумма центрального ряда" in lower:
            continue
        if text.startswith("P≥"):
            text = re.sub(r"\s*\(\d+\s*/\s*\d+\)\s*$", "", text)
            text = text.replace("/интервал", "")
            match = re.search(r"P≥\s*([0-9]+(?:,[0-9]+)?)\s*мм(?:/([0-9]+)\s*ч)?\s*:\s*([0-9]+(?:,[0-9]+)?)\s*%", text)
            if not match:
                body.append(text)
                continue
            threshold = float(match.group(1).replace(",", "."))
            probability = float(match.group(3).replace(",", "."))
            if probability <= 0:
                continue
            if threshold >= 5.0 and probability < 5.0:
                continue
            compact = f"P≥{match.group(1)}"
            if match.group(2):
                compact += f"/{match.group(2)} ч"
            compact += f": {match.group(3)} %"
            probability_lines.append((threshold, probability, compact))
            continue

        if lower.startswith("без существенных осадков"):
            body.append("медиана 0 мм" if ensemble else "без осадков")
            continue

        text = text.replace(" мм за ", " мм / ")
        if ensemble and (control or has_spread) and re.match(r"^[0-9−+.,]", text) and not text.lower().startswith("медиана"):
            text = "медиана " + text
        body.append(text)

    if len(probability_lines) > 2:
        # Keep occurrence probability plus the most severe non-zero threshold.
        first = min(probability_lines, key=lambda item: item[0])
        severe = max(probability_lines, key=lambda item: item[0])
        probability_lines = [first, severe] if severe != first else [first]
    else:
        probability_lines.sort(key=lambda item: item[0])

    result = body + [item[2] for item in probability_lines]
    return "\n".join(result or ["—"])


def _summary_parts(line: str) -> tuple[str, str]:
    text = _pretty_cell(line).strip()
    if ":" in text:
        label, value = text.split(":", 1)
        return label.strip(), value.strip()
    return "Прогноз", text

def _page(plt: Any):
    fig = plt.figure(figsize=A4_LANDSCAPE, dpi=PDF_DPI)
    fig.patch.set_facecolor("white")
    return fig


def _wrap_cell(value: Any, width: int) -> str:
    lines: list[str] = []
    for source in str(value or "—").splitlines() or ["—"]:
        parts = textwrap.wrap(
            source,
            width=max(6, int(width)),
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=False,
        )
        lines.extend(parts or [""])
    return "\n".join(lines)


def _wrap_text(value: str, width: int) -> str:
    return "\n".join(
        textwrap.wrap(
            str(value),
            width=max(20, int(width)),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
    )


def _weekday_ru(index: int) -> str:
    return ("пн", "вт", "ср", "чт", "пт", "сб", "вс")[int(index) % 7]


def _footer(fig: Any) -> None:
    fig.text(0.045, 0.022, "GFS Profile • модельный прогноз", fontsize=6.4, color=MUTED, va="bottom")
    fig.text(0.955, 0.022, "местное время точки • не наблюдение", fontsize=6.4, color=MUTED, ha="right", va="bottom")
