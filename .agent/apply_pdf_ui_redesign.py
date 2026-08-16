from __future__ import annotations

from pathlib import Path


PDF_RENDERER = r'''from __future__ import annotations

"""Compact native PDF rendering for meteogram reports.

The layout is intentionally information-dense: one overview page with the
meteogram, followed by compact tables. Row heights depend on actual content,
so short forecasts do not produce mostly empty pages.
"""

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

    meta = "  •  ".join(
        item for item in (str(data.period_line), str(data.source_line)) if item
    )
    fig.text(0.045, 0.884, _wrap_text(meta, 150), fontsize=7.4, color=MUTED, va="top", linespacing=1.15)
    fig.text(0.045, 0.848, _wrap_text(str(data.point_line), 150), fontsize=7.4, color=MUTED, va="top")

    lines = list(getattr(data, "main_lines", ()) or ())[:6]
    panel_top = 0.812
    panel_height = _summary_height(lines)
    panel_bottom = panel_top - panel_height
    panel = FancyBboxPatch(
        (0.042, panel_bottom),
        0.916,
        panel_height,
        boxstyle="round,pad=0.004,rounding_size=0.005",
        linewidth=0.6,
        edgecolor=GRID,
        facecolor=SUMMARY_BG,
        transform=fig.transFigure,
        clip_on=False,
    )
    fig.add_artist(panel)
    fig.text(0.055, panel_top - 0.018, "Кратко", fontsize=9.2, fontweight="bold", color=INK, va="top")
    _draw_summary_lines(fig, lines, panel_top - 0.052, panel_bottom + 0.014)

    chart_bottom = 0.072
    chart_top = panel_bottom - 0.025
    fig.text(0.045, chart_top + 0.008, "Метеограмма", fontsize=9.0, fontweight="bold", color=INK, va="bottom")
    chart_ax = fig.add_axes([0.045, chart_bottom, 0.91, max(0.25, chart_top - chart_bottom)])
    chart_ax.imshow(image)
    chart_ax.axis("off")

    _footer(fig)
    pdf.savefig(fig)
    plt.close(fig)


def _detail_pages(pdf: Any, plt: Any, data: Any) -> None:
    ensemble = _is_ensemble(data)
    daily = _daily_spec(data, ensemble)
    control = _control_spec(data, ensemble)
    notes_height = _notes_height(data)

    daily_h = _table_height(daily[1], daily[2]) if daily[1] else 0.0
    control_h = _table_height(control[1], control[2]) if control[1] else 0.0
    combined = (
        (0.037 + daily_h if daily[1] else 0.0)
        + (0.024 if daily[1] and control[1] else 0.0)
        + (0.037 + control_h if control[1] else 0.0)
        + notes_height
        + 0.05
    )

    if combined <= 0.80:
        fig = _page(plt)
        fig.text(0.045, 0.955, "Прогноз в деталях", fontsize=13.3, fontweight="bold", color=INK, va="top")
        y = 0.905
        if daily[1]:
            y = _draw_table(fig, plt, y, "По суткам", *daily)
        if control[1]:
            y -= 0.006
            y = _draw_table(fig, plt, y, "По срокам", *control)
        _draw_notes(fig, y - 0.004, data)
        _footer(fig)
        pdf.savefig(fig)
        plt.close(fig)
        return

    if daily[1]:
        for index, batch in enumerate(_paginate_rows(daily[1], daily[2], 0.69), 1):
            fig = _page(plt)
            suffix = "" if len(daily[1]) <= len(batch) else f" · {index}"
            fig.text(0.045, 0.955, "Прогноз по суткам" + suffix, fontsize=13.3, fontweight="bold", color=INK, va="top")
            _draw_table(fig, plt, 0.905, "", daily[0], batch, daily[2], daily[3], daily[4])
            _footer(fig)
            pdf.savefig(fig)
            plt.close(fig)

    if control[1]:
        batches = _paginate_rows(control[1], control[2], 0.66)
        for index, batch in enumerate(batches, 1):
            fig = _page(plt)
            suffix = f" · {index}/{len(batches)}" if len(batches) > 1 else ""
            fig.text(0.045, 0.955, "Прогноз по срокам" + suffix, fontsize=13.3, fontweight="bold", color=INK, va="top")
            y = _draw_table(fig, plt, 0.905, "", control[0], batch, control[2], control[3], control[4])
            if index == len(batches):
                _draw_notes(fig, y - 0.010, data)
            _footer(fig)
            pdf.savefig(fig)
            plt.close(fig)


def _daily_spec(data: Any, ensemble: bool):
    rows = list(getattr(data, "daily_rows", ()) or ())
    if ensemble:
        headers = ("Дата", "Погода", "Температура", "Осадки", "Ветер", "Давление", "Ансамбль")
        wrap = (9, 22, 19, 27, 22, 15, 20)
        widths = (0.07, 0.15, 0.145, 0.19, 0.17, 0.115, 0.16)
    else:
        headers = ("Дата", "Погода", "Температура", "Осадки", "Ветер", "Давление")
        wrap = (9, 24, 21, 31, 25, 17)
        widths = (0.08, 0.18, 0.16, 0.22, 0.20, 0.16)
    values = []
    for row in rows:
        base = [
            f"{row.day:%d.%m}\n{_weekday_ru(row.day.weekday())}",
            row.weather,
            row.temperature,
            row.precipitation,
            row.wind,
            row.pressure,
        ]
        if ensemble:
            base.append(row.ensemble)
        values.append(base)
    return headers, values, wrap, widths, 6.7


def _control_spec(data: Any, ensemble: bool):
    rows = list(getattr(data, "control_rows", ()) or ())
    if ensemble:
        headers = ("Срок", "T / Td", "RH / облака", "Осадки", "Ветер", "Давление", "Ансамбль")
        wrap = (10, 19, 23, 30, 25, 16, 18)
        widths = (0.08, 0.13, 0.16, 0.21, 0.17, 0.12, 0.13)
    else:
        headers = ("Срок", "T / Td", "RH / облака", "Осадки", "Ветер", "Давление")
        wrap = (10, 21, 26, 34, 29, 17)
        widths = (0.09, 0.15, 0.18, 0.23, 0.20, 0.15)
    values = []
    for row in rows:
        base = [
            f"{row.time:%d.%m}\n{row.time:%H:%M}",
            row.temperature,
            row.humidity_cloud,
            row.precipitation,
            row.wind,
            row.pressure,
        ]
        if ensemble:
            base.append(row.ensemble)
        values.append(base)
    return headers, values, wrap, widths, 6.35


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
    methods = list(getattr(data, "method_lines", ()) or ())[:5]
    warnings = list(getattr(data, "warning_lines", ()) or ())[:4]
    if not methods and not warnings:
        return top

    if top < 0.085:
        return top
    fig.text(0.045, top, "Примечания", fontsize=8.2, fontweight="bold", color=INK, va="top")
    y = top - 0.025
    for line in methods:
        wrapped = _wrap_text("• " + str(line), 145)
        fig.text(0.055, y, wrapped, fontsize=6.7, color=MUTED, va="top", linespacing=1.12)
        y -= 0.018 * (wrapped.count("\n") + 1)
        if y < 0.070:
            return y

    if warnings and y > 0.085:
        from matplotlib.patches import FancyBboxPatch

        warning_text = "   ".join("• " + str(item) for item in warnings)
        wrapped = _wrap_text(warning_text, 145)
        box_h = 0.022 + 0.018 * (wrapped.count("\n") + 1)
        box_bottom = max(0.055, y - box_h)
        box = FancyBboxPatch(
            (0.045, box_bottom), 0.91, box_h,
            boxstyle="round,pad=0.003,rounding_size=0.004",
            linewidth=0.4, edgecolor="#E5CFA8", facecolor=WARNING_BG,
            transform=fig.transFigure, clip_on=False,
        )
        fig.add_artist(box)
        fig.text(0.055, y - 0.010, wrapped, fontsize=6.55, color="#6B4A25", va="top", linespacing=1.12)
        y = box_bottom
    return y


def _draw_summary_lines(fig: Any, lines: Sequence[str], top: float, bottom: float) -> None:
    if not lines:
        fig.text(0.055, top, "Существенных особенностей не выделено.", fontsize=7.7, color=INK, va="top")
        return
    split = (len(lines) + 1) // 2 if len(lines) > 3 else len(lines)
    columns = (lines[:split], lines[split:]) if len(lines) > 3 else (lines, ())
    for col_index, column in enumerate(columns):
        if not column:
            continue
        x = 0.055 if col_index == 0 else 0.515
        y = top
        width = 68 if len(lines) > 3 else 135
        for line in column:
            wrapped = _wrap_text("• " + str(line), width)
            fig.text(x, y, wrapped, fontsize=7.55, color=INK, va="top", linespacing=1.10)
            y -= 0.020 * (wrapped.count("\n") + 1) + 0.004
            if y < bottom:
                break


def _summary_height(lines: Sequence[str]) -> float:
    if not lines:
        return 0.10
    if len(lines) <= 3:
        counts = [len(_wrap_text("• " + str(line), 135).splitlines()) for line in lines]
        used = sum(counts)
    else:
        split = (len(lines) + 1) // 2
        left = sum(len(_wrap_text("• " + str(line), 68).splitlines()) for line in lines[:split])
        right = sum(len(_wrap_text("• " + str(line), 68).splitlines()) for line in lines[split:])
        used = max(left, right)
    return min(0.205, max(0.115, 0.062 + used * 0.021))


def _table_height(rows: Sequence[Sequence[str]], wrap_widths: Sequence[int]) -> float:
    wrapped = [[_wrap_cell(value, width) for value, width in zip(row, wrap_widths)] for row in rows]
    return 0.036 + sum(_row_heights(wrapped))


def _row_heights(rows: Sequence[Sequence[str]]) -> list[float]:
    result = []
    for row in rows:
        lines = max((str(cell).count("\n") + 1 for cell in row), default=1)
        result.append(min(0.050, 0.028 + 0.006 * max(0, lines - 1)))
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
    methods = list(getattr(data, "method_lines", ()) or ())[:5]
    warnings = list(getattr(data, "warning_lines", ()) or ())[:4]
    if not methods and not warnings:
        return 0.0
    method_lines = sum(len(_wrap_text("• " + str(item), 145).splitlines()) for item in methods)
    warning_lines = sum(len(_wrap_text("• " + str(item), 145).splitlines()) for item in warnings)
    return min(0.19, 0.034 + method_lines * 0.018 + (0.018 + warning_lines * 0.018 if warnings else 0.0))


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
        pad = 14
        y0 = max(0, int(ys.min()) - pad)
        y1 = min(array.shape[0], int(ys.max()) + pad + 1)
        x0 = max(0, int(xs.min()) - pad)
        x1 = min(array.shape[1], int(xs.max()) + pad + 1)
        return array[y0:y1, x0:x1]
    except Exception:
        return image


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
'''


MAIN_LINES = r'''def _build_main_lines(series: Any, daily_rows: Sequence[ReportDay]) -> list[str]:
    times = list(series.times)
    temperature = _values(series, "temperature_2m")
    wind = _values(series, "wind_speed_10m")
    gust = _values(series, "wind_gusts_10m")
    humidity = _values(series, "relative_humidity_2m")
    ensemble = bool(getattr(series.source, "ensemble", False))

    lines: list[str] = []
    min_index = _nanargmin(temperature)
    max_index = _nanargmax(temperature)
    if min_index is not None and max_index is not None:
        line = (
            f"Температура: {_fmt_signed(temperature[min_index], 1)}…{_fmt_signed(temperature[max_index], 1)} °C; "
            f"минимум {times[min_index]:%d.%m %H:%M}, максимум {times[max_index]:%d.%m %H:%M}."
        )
        if ensemble:
            q10 = _statistic(series, "temperature_2m", "q10")
            q90 = _statistic(series, "temperature_2m", "q90")
            low = _nanmin(q10)
            high = _nanmax(q90)
            if low is not None and high is not None:
                line += f" q10-q90: {_fmt_signed(low, 1)}…{_fmt_signed(high, 1)} °C."
        lines.append(line)

    daily_amounts: list[tuple[float, str]] = []
    for row in daily_rows:
        match = re.match(r"([0-9]+(?:,[0-9]+)?)", row.precipitation)
        if match:
            daily_amounts.append((float(match.group(1).replace(",", ".")), f"{row.day:%d.%m}"))
    if daily_amounts:
        amount, day_label = max(daily_amounts)
        if ensemble and _has_daily_statistic(series, "precipitation", "q50"):
            line = f"Осадки: максимальная суточная медиана {_fmt(amount, 1)} мм {day_label}."
        else:
            line = f"Осадки: наибольшая суточная сумма {_fmt(amount, 1)} мм {day_label}."
    else:
        line = "Осадки: существенной суточной суммы не выделяется."
    if ensemble:
        max_probability = _max_probability(series, range(len(times)))
        if max_probability is not None:
            line += f" Максимальная вероятность >=0,1 мм: {_fmt(max_probability, 0)} %."
    lines.append(line)

    max_wind_index = _nanargmax(wind)
    risk_gust = _statistic(series, "wind_gusts_10m", "q90") if ensemble else gust
    max_gust_index = _nanargmax(risk_gust)
    wind_parts = []
    if max_wind_index is not None:
        wind_parts.append(f"до {_fmt(wind[max_wind_index], 1)} м/с {times[max_wind_index]:%d.%m %H:%M}")
    if max_gust_index is not None:
        label = "q90 порывов" if ensemble else "порывы"
        wind_parts.append(f"{label} до {_fmt(risk_gust[max_gust_index], 1)} м/с {times[max_gust_index]:%d.%m %H:%M}")
    if wind_parts:
        lines.append("Ветер: " + "; ".join(wind_parts) + ".")

    high_humidity = np.flatnonzero(np.isfinite(humidity) & (humidity >= 95.0))
    if high_humidity.size:
        first = int(high_humidity[0])
        lines.append(f"Высокая влажность: RH >=95 % с {times[first]:%d.%m %H:%M}.")

    if ensemble:
        counts = _values(series, "ensemble_member_count")
        minimum = _nanmin(counts)
        observed = int(round(minimum)) if minimum is not None else int(getattr(series, "member_count", 0) or 0)
        expected = int(getattr(series, "expected_member_count", 0) or observed)
        lines.append(f"Ансамбль: не менее {observed}/{expected or observed} членов на срок.")
    return lines[:5]
'''


METHOD_LINES = r'''def _build_method_lines(series: Any) -> list[str]:
    ensemble = bool(getattr(series.source, "ensemble", False))
    lines = [
        "Время и суточные границы - местные.",
        "Осадки - за исходный интервал модели; ветер - направление, откуда дует.",
    ]
    if ensemble:
        lines.append("Центр ансамбля: среднее для T/Td/давления, медиана для остальных параметров; диапазоны q25-q75 и q10-q90.")
        lines.append("Вероятность осадков - доля доступных членов ансамбля, превысивших порог.")
        if not _has_daily_statistic(series, "precipitation", "q50"):
            lines.append("Суточная статистика по отдельным членам недоступна; в таблице используется сумма центрального ряда.")
    sampling = str(getattr(series, "sampling_mode", ""))
    if sampling == "raw_model_grid":
        lines.append("Расчёт выполнен для модельной ячейки без высотной коррекции.")
    lines.append("Модельный прогноз, не наблюдение.")
    return lines
'''


def replace_block(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement.rstrip() + "\n\n" + text[end + 1 :]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label}: fragment not found")
    return text.replace(old, new, 1)


def main() -> None:
    Path("meteogram_pdf.py").write_text(PDF_RENDERER, encoding="utf-8")

    report_path = Path("meteogram_report.py")
    report = report_path.read_text(encoding="utf-8")
    report = replace_block(report, "def _build_main_lines", "\ndef _build_method_lines", MAIN_LINES)
    report = replace_block(report, "def _build_method_lines", "\ndef _build_warning_lines", METHOD_LINES)
    report = replace_once(
        report,
        "Важно: один модельный ансамбль или одна модель; не радиозонд, не станция и не официальный выпуск предупреждения.",
        "Модельный прогноз. Не наблюдение и не официальный выпуск предупреждений.",
        "DOCX disclaimer",
    )
    report_path.write_text(report, encoding="utf-8")

    telegram_path = Path("telegram_meteogram.py")
    telegram = telegram_path.read_text(encoding="utf-8")
    replacements = (
        (
            "📄 DOCX / 🧾 PDF — сводка, таблицы по суткам и срокам, текст и метеограмма.",
            "📄 DOCX / 🧾 PDF — краткая сводка, таблицы по суткам и срокам и метеограмма.",
        ),
        ("1/{total_steps} Проверяю источник и период…", "1/{total_steps} Загружаю прогноз…"),
        ("f\"1/{total_steps} Проверяю источник и период…\"", "f\"1/{total_steps} Загружаю прогноз…\""),
        ("f\"4/{total_steps} Строю метеограмму без пересечений подписей…\"", "f\"4/{total_steps} Строю метеограмму…\""),
        ("f\"5/{total_steps} Формирую {_output_label(output_format)}…\"", "f\"5/{total_steps} Формирую файл…\""),
        ("f\"{total_steps}/{total_steps} Отправляю результат…\"", "f\"{total_steps}/{total_steps} Отправляю файл…\""),
        ("⚠️ PDF недоступен на сервере; отправляю полноценный DOCX.", "⚠️ PDF создать не удалось; отправляю DOCX."),
        ("PDF сформировать не удалось. DOCX содержит ту же сводку, таблицы, текст и метеограмму.", "PDF создать не удалось. Отправляю DOCX с тем же прогнозом."),
    )
    for old, new in replacements:
        if old in telegram:
            telegram = telegram.replace(old, new)
    if "без пересечений подписей" in telegram or "полноценный DOCX" in telegram:
        raise RuntimeError("telegram_meteogram.py: AI-like wording remains")
    telegram_path.write_text(telegram, encoding="utf-8")

    models_path = Path("meteogram_models.py")
    models = models_path.read_text(encoding="utf-8")
    models = replace_once(models, '"ECMWF AIFS 0.25° Single"', '"ECMWF AIFS 0.25°"', "AIFS model label")
    models_path.write_text(models, encoding="utf-8")

    test_path = Path("tests/test_meteogram_pdf_ui.py")
    test_path.write_text(r'''from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from meteogram_models import source_for_id
from meteogram_report import build_meteogram_report_data, write_meteogram_report
from meteogram_report_smoke import _series


class MeteogramPdfUiTests(unittest.TestCase):
    def test_report_copy_is_concise(self) -> None:
        data = build_meteogram_report_data(_series())
        text = "\n".join(data.main_lines + data.method_lines)
        self.assertNotIn("графические тренды", text)
        self.assertNotIn("профиль не является", text)
        self.assertNotIn("межмодельным консенсусом", text)
        self.assertIn("Время и суточные границы - местные.", text)

    def test_aifs_name_has_no_single_suffix(self) -> None:
        self.assertEqual(source_for_id("ecmwf_aifs").model, "ECMWF AIFS 0.25°")

    def test_short_pdf_is_compact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            chart = directory / "meteogram.png"
            Image.new("RGB", (1600, 1000), "white").save(chart)
            result = write_meteogram_report(
                _series(), chart, "pdf", output_dir=directory, pdf_fallback_to_docx=False
            )
            payload = result.path.read_bytes()
            pages = len(re.findall(rb"/Type\s*/Page\b", payload))
            self.assertGreaterEqual(pages, 2)
            self.assertLessEqual(pages, 3)

    def test_telegram_copy_has_no_layout_debugging_language(self) -> None:
        source = Path("telegram_meteogram.py").read_text(encoding="utf-8")
        self.assertNotIn("без пересечений подписей", source)
        self.assertNotIn("полноценный DOCX", source)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

    docs = Path("docs/METEOGRAM.md")
    doc_text = docs.read_text(encoding="utf-8").rstrip()
    marker = "## Компоновка PDF"
    if marker not in doc_text:
        doc_text += "\n\n## Компоновка PDF\n\nPDF использует компактную A4 landscape-компоновку: на первой странице — сводка и метеограмма, далее — таблицы с высотой строк по содержимому. Для одиночной модели не выводится пустая колонка ансамбля; примечания размещаются рядом с данными, а не на отдельной почти пустой странице. Служебные детали построения графика в Telegram-пользовательский текст не выводятся.\n"
    docs.write_text(doc_text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
