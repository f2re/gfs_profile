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


DRAW_NOTES = r'''def _draw_notes(fig: Any, top: float, data: Any) -> float:
    methods = list(getattr(data, "method_lines", ()) or ())[:6]
    warnings = list(getattr(data, "warning_lines", ()) or ())[:4]
    if not methods and not warnings:
        return top
    if top < 0.070:
        return top

    fig.text(0.045, top, "Примечания", fontsize=7.8, fontweight="bold", color=INK, va="top")
    y = top - 0.022
    if methods:
        method_text = " • ".join(str(line).rstrip(".") for line in methods) + "."
        wrapped = _wrap_text(method_text, 155)
        fig.text(0.055, y, wrapped, fontsize=6.45, color=MUTED, va="top", linespacing=1.10)
        y -= 0.016 * (wrapped.count("\n") + 1) + 0.005

    if warnings and y > 0.055:
        warning_text = " • ".join(str(line).rstrip(".") for line in warnings) + "."
        wrapped = _wrap_text(warning_text, 155)
        fig.text(0.055, y, wrapped, fontsize=6.35, color="#765527", va="top", linespacing=1.10)
        y -= 0.016 * (wrapped.count("\n") + 1)
    return y
'''


NOTES_HEIGHT = r'''def _notes_height(data: Any) -> float:
    methods = list(getattr(data, "method_lines", ()) or ())[:6]
    warnings = list(getattr(data, "warning_lines", ()) or ())[:4]
    if not methods and not warnings:
        return 0.0
    method_text = " • ".join(str(line).rstrip(".") for line in methods)
    method_lines = len(_wrap_text(method_text, 155).splitlines()) if method_text else 0
    warning_text = " • ".join(str(line).rstrip(".") for line in warnings)
    warning_lines = len(_wrap_text(warning_text, 155).splitlines()) if warning_text else 0
    return 0.029 + method_lines * 0.016 + (0.005 + warning_lines * 0.016 if warning_lines else 0.0)
'''


CONTROL_INDICES = r'''def _control_indices(times: Sequence[datetime]) -> list[int]:
    if not times:
        return []
    seconds = np.asarray([item.timestamp() for item in times], dtype=float)
    start = seconds[0]
    end_hours = max(0.0, (seconds[-1] - start) / 3600.0)
    targets: list[float] = []

    # Near-term detail is useful; farther out, 12-hour checkpoints keep the
    # report readable without repeating essentially the same information.
    hour = 0.0
    while hour <= min(24.0, end_hours) + 0.01:
        targets.append(hour)
        hour += 6.0
    hour = 36.0
    while hour <= end_hours + 0.01:
        targets.append(hour)
        hour += 12.0

    indices: list[int] = []
    for target in targets:
        index = int(np.argmin(np.abs((seconds - start) / 3600.0 - target)))
        if not indices or index != indices[-1]:
            indices.append(index)
    if indices and indices[-1] != len(times) - 1 and end_hours - targets[-1] >= 6.0:
        indices.append(len(times) - 1)
    return indices
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
    pdf = replace_block(pdf, "def _draw_notes", "\ndef _draw_summary_lines", DRAW_NOTES)
    pdf = replace_block(pdf, "def _notes_height", "\ndef _is_ensemble", NOTES_HEIGHT)
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
    report = replace_block(report, "def _control_indices", "\ndef _format_wind", CONTROL_INDICES)
    report = report.replace(
        "Шаг: через 6 часов в первые 72 часа, далее через 12 часов. Значения относятся к ближайшему доступному сроку модели.",
        "Шаг: через 6 часов в первые сутки, далее через 12 часов. Значения относятся к ближайшему доступному сроку модели.",
    )
    report_path.write_text(report, encoding="utf-8")

    docs = Path("docs/METEOGRAM.md")
    text = docs.read_text(encoding="utf-8")
    text = text.replace("каждые 6 часов первые 72 часа, затем каждые 12 часов", "каждые 6 часов первые сутки, затем каждые 12 часов")
    text = text.replace("через 6 часов в первые 72 часа, далее через 12 часов", "через 6 часов в первые сутки, далее через 12 часов")
    docs.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
