from __future__ import annotations

from pathlib import Path


DETAIL_PAGES = r'''def _detail_pages(pdf: Any, plt: Any, data: Any) -> None:
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
            if need <= max(0.0, y - 0.055):
                _draw_notes(fig, y - 0.004, data)
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
'''


TAKE_ROWS = r'''def _take_rows(
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
'''


def replace_block(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement.rstrip() + "\n\n" + text[end + 1 :]


def main() -> None:
    pdf_path = Path("meteogram_pdf.py")
    pdf = pdf_path.read_text(encoding="utf-8")
    pdf = replace_block(pdf, "def _detail_pages", "\ndef _daily_spec", DETAIL_PAGES)
    insert_at = pdf.index("\ndef _daily_spec")
    pdf = pdf[:insert_at] + "\n\n" + TAKE_ROWS.rstrip() + "\n" + pdf[insert_at:]
    pdf = pdf.replace("return headers, values, wrap, widths, 6.7", "return headers, values, wrap, widths, 6.9")
    pdf = pdf.replace("return headers, values, wrap, widths, 6.35", "return headers, values, wrap, widths, 6.55")
    pdf_path.write_text(pdf, encoding="utf-8")

    report_path = Path("meteogram_report.py")
    report = report_path.read_text(encoding="utf-8")
    old = "probability_parts = _probability_parts(series, indices)\n        if probability_parts:"
    new = "probability_parts = _probability_parts(series, indices) if ensemble else []\n        if probability_parts:"
    if old not in report:
        raise RuntimeError("daily probability block not found")
    report = report.replace(old, new, 1)

    old_control = "probability_parts = _probability_parts(series, [index])\n        if probability_parts:"
    new_control = "probability_parts = _probability_parts(series, [index])[:1] if ensemble else []\n        if probability_parts:"
    if old_control not in report:
        raise RuntimeError("control probability block not found")
    report = report.replace(old_control, new_control, 1)
    report_path.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
