from __future__ import annotations

"""Native PDF rendering for meteogram reports.

The PDF is rendered directly with Matplotlib, which is already a runtime
requirement of the project.  This keeps PDF generation independent from an
external office suite while preserving the same report data used by DOCX.
"""

import textwrap
from pathlib import Path
from typing import Any, Iterable, Sequence


A4_LANDSCAPE = (11.69, 8.27)
PDF_DPI = 144


class MeteogramPdfError(RuntimeError):
    pass


def write_meteogram_pdf(
    report_data: Any,
    chart_path: str | Path,
    output_path: str | Path,
) -> Path:
    """Write a self-contained landscape A4 PDF from ``MeteogramReportData``."""

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
    except Exception as exc:  # pragma: no cover - runtime_check covers imports
        raise MeteogramPdfError(f"Не удалось загрузить PDF-рендерер: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)

    try:
        image = mpimg.imread(chart)
        metadata = {
            "Title": str(getattr(report_data, "title", "Метеограмма")),
            "Author": "GFS Profile Bot",
            "Subject": "Модельный прогноз. Не наблюдение.",
        }
        with PdfPages(temporary, metadata=metadata) as pdf:
            _overview_page(pdf, plt, report_data, image)

            daily_rows = list(getattr(report_data, "daily_rows", ()) or ())
            if daily_rows:
                daily_table = [
                    [
                        f"{row.day:%d.%m}\n{_weekday_ru(row.day.weekday())}",
                        row.weather,
                        row.temperature,
                        row.precipitation,
                        row.wind,
                        row.pressure,
                        row.ensemble,
                    ]
                    for row in daily_rows
                ]
                _table_pages(
                    pdf,
                    plt,
                    title="Прогноз по суткам",
                    headers=("Дата", "Явления", "Температура", "Осадки", "Ветер", "Давление", "Ансамбль"),
                    rows=daily_table,
                    wrap_widths=(9, 22, 18, 28, 24, 15, 21),
                    column_widths=(0.075, 0.15, 0.135, 0.205, 0.17, 0.105, 0.16),
                    rows_per_page=7,
                )

            control_rows = list(getattr(report_data, "control_rows", ()) or ())
            if control_rows:
                control_table = [
                    [
                        f"{row.time:%d.%m}\n{row.time:%H:%M}",
                        row.temperature,
                        row.humidity_cloud,
                        row.precipitation,
                        row.wind,
                        row.pressure,
                        row.ensemble,
                    ]
                    for row in control_rows
                ]
                _table_pages(
                    pdf,
                    plt,
                    title="Контрольные сроки",
                    headers=("Срок", "Температура", "Влажн./облака", "Осадки", "Ветер", "Давление", "Ансамбль"),
                    rows=control_table,
                    wrap_widths=(10, 18, 21, 30, 25, 15, 21),
                    column_widths=(0.075, 0.12, 0.135, 0.22, 0.18, 0.105, 0.165),
                    rows_per_page=9,
                )

            _method_page(pdf, plt, report_data)

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
    fig = plt.figure(figsize=A4_LANDSCAPE, dpi=PDF_DPI)
    fig.patch.set_facecolor("white")

    fig.text(0.045, 0.955, str(data.title), fontsize=16, fontweight="bold", va="top")
    fig.text(0.045, 0.912, str(data.subtitle), fontsize=11.5, fontweight="bold", va="top")
    fig.text(0.045, 0.878, str(data.point_line), fontsize=8.6, va="top")
    fig.text(0.045, 0.850, str(data.period_line), fontsize=8.4, va="top")
    fig.text(0.045, 0.823, str(data.source_line), fontsize=8.2, va="top")

    fig.text(0.045, 0.782, "Главное", fontsize=11.5, fontweight="bold", va="top")
    y = 0.748
    for line in list(getattr(data, "main_lines", ()) or ())[:8]:
        wrapped = _wrap_text("• " + str(line), 105)
        fig.text(0.055, y, wrapped, fontsize=8.4, va="top", linespacing=1.18)
        y -= 0.027 * max(1, wrapped.count("\n") + 1) + 0.006

    chart_top = max(0.34, min(0.59, y - 0.015))
    chart_ax = fig.add_axes([0.045, 0.055, 0.91, chart_top - 0.055])
    chart_ax.imshow(image)
    chart_ax.axis("off")
    chart_ax.set_title("Метеограмма", fontsize=10, fontweight="bold", pad=5)

    _footer(fig, "Модельный прогноз. Не радиозонд, не радар и не наблюдение.")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _table_pages(
    pdf: Any,
    plt: Any,
    *,
    title: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    wrap_widths: Sequence[int],
    column_widths: Sequence[float],
    rows_per_page: int,
) -> None:
    total_pages = max(1, (len(rows) + rows_per_page - 1) // rows_per_page)
    for page_index, start in enumerate(range(0, len(rows), rows_per_page), 1):
        batch = rows[start : start + rows_per_page]
        wrapped_rows = [
            [_wrap_cell(value, width) for value, width in zip(row, wrap_widths)]
            for row in batch
        ]

        fig = plt.figure(figsize=A4_LANDSCAPE, dpi=PDF_DPI)
        fig.patch.set_facecolor("white")
        suffix = f" · {page_index}/{total_pages}" if total_pages > 1 else ""
        fig.text(0.045, 0.955, title + suffix, fontsize=14, fontweight="bold", va="top")

        ax = fig.add_axes([0.035, 0.075, 0.93, 0.83])
        ax.axis("off")
        table = ax.table(
            cellText=wrapped_rows,
            colLabels=list(headers),
            colWidths=list(column_widths),
            cellLoc="left",
            colLoc="center",
            loc="upper center",
            bbox=[0.0, 0.0, 1.0, 1.0],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(6.8)

        row_line_counts = [
            max((str(cell).count("\n") + 1 for cell in row), default=1)
            for row in wrapped_rows
        ]
        row_weights = [1.25] + [max(1.15, min(3.3, 0.78 + 0.62 * count)) for count in row_line_counts]
        total_weight = sum(row_weights)

        for (row_index, col_index), cell in table.get_celld().items():
            cell.set_edgecolor("0.72")
            cell.set_linewidth(0.55)
            cell.PAD = 0.035
            if row_index == 0:
                cell.set_text_props(weight="bold", ha="center", va="center", fontsize=7.1)
                cell.set_facecolor("0.92")
                cell.set_height(row_weights[0] / total_weight)
            else:
                cell.set_text_props(ha="left", va="center", fontsize=6.75)
                cell.set_height(row_weights[row_index] / total_weight)
                if col_index == 0:
                    cell.set_text_props(ha="center", va="center", fontsize=6.75)

        _footer(fig, "GFS Profile Bot · модельный прогноз · локальное время точки")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def _method_page(pdf: Any, plt: Any, data: Any) -> None:
    fig = plt.figure(figsize=A4_LANDSCAPE, dpi=PDF_DPI)
    fig.patch.set_facecolor("white")
    fig.text(0.045, 0.955, "Методика и ограничения", fontsize=14, fontweight="bold", va="top")

    y = 0.905
    for line in list(getattr(data, "method_lines", ()) or ()):
        wrapped = _wrap_text("• " + str(line), 120)
        fig.text(0.055, y, wrapped, fontsize=8.5, va="top", linespacing=1.2)
        y -= 0.031 * max(1, wrapped.count("\n") + 1) + 0.009
        if y < 0.42:
            break

    warnings = list(getattr(data, "warning_lines", ()) or ())
    if warnings:
        y = min(y - 0.018, 0.38)
        fig.text(0.045, y, "Предупреждения о данных", fontsize=11.5, fontweight="bold", va="top")
        y -= 0.045
        for line in warnings[:8]:
            wrapped = _wrap_text("• " + str(line), 120)
            fig.text(0.055, y, wrapped, fontsize=8.4, va="top", linespacing=1.2)
            y -= 0.031 * max(1, wrapped.count("\n") + 1) + 0.008
            if y < 0.10:
                break

    fig.text(
        0.045,
        0.075,
        "PDF сформирован нативным Python-рендерером; внешний LibreOffice для штатной работы не требуется.",
        fontsize=7.7,
        style="italic",
        va="bottom",
    )
    _footer(fig, "Модельный прогноз. Интерпретировать с учётом разрешения и неопределённости модели.")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _wrap_cell(value: Any, width: int) -> str:
    lines: list[str] = []
    source_lines = str(value or "—").splitlines() or ["—"]
    for source in source_lines:
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
        )
        or [""]
    )


def _weekday_ru(index: int) -> str:
    return ("пн", "вт", "ср", "чт", "пт", "сб", "вс")[int(index) % 7]


def _footer(fig: Any, text: str) -> None:
    fig.text(0.955, 0.022, text, fontsize=6.7, ha="right", va="bottom", color="0.35")
