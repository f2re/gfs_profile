from __future__ import annotations

from pathlib import Path


def replace_block(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label}: fragment not found")
    return text.replace(old, new, 1)


OVERVIEW = r'''def _overview_page(pdf: Any, plt: Any, data: Any, image: Any) -> None:
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
'''


DAILY_SPEC = r'''def _daily_spec(data: Any, ensemble: bool):
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
'''


CONTROL_SPEC = r'''def _control_spec(data: Any, ensemble: bool):
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
'''


SUMMARY_DRAW = r'''def _draw_summary_lines(fig: Any, lines: Sequence[str], top: float, bottom: float) -> None:
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
'''


SUMMARY_HEIGHT = r'''def _summary_height(lines: Sequence[str]) -> float:
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
'''


ROW_HEIGHTS = r'''def _row_heights(rows: Sequence[Sequence[str]]) -> list[float]:
    result = []
    for row in rows:
        lines = max((str(cell).count("\n") + 1 for cell in row), default=1)
        # Do not cap multi-line cells so tightly that text can overlap the next row.
        result.append(min(0.095, max(0.034, 0.028 + 0.0115 * max(0, lines - 1))))
    return result
'''


DRAW_NOTES = r'''def _draw_notes(fig: Any, top: float, data: Any) -> float:
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
'''


NOTES_HEIGHT = r'''def _notes_height(data: Any) -> float:
    methods = list(getattr(data, "method_lines", ()) or ())[:6]
    warnings = list(getattr(data, "warning_lines", ()) or ())[:4]
    if not methods and not warnings:
        return 0.0
    method_text = " • ".join(str(line).rstrip(".") for line in methods)
    method_lines = len(_wrap_text(method_text, 148).splitlines()) if method_text else 0
    warning_text = " • ".join(str(line).rstrip(".") for line in warnings)
    warning_lines = len(_wrap_text(warning_text, 148).splitlines()) if warning_text else 0
    return 0.031 + method_lines * 0.0175 + (0.006 + warning_lines * 0.0175 if warning_lines else 0.0)
'''


CROP_IMAGE = r'''def _crop_image(image: Any) -> Any:
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
'''


HELPERS = r'''def _pretty_cell(value: Any) -> str:
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
'''


FINISH_AXES = r'''def _finish_axes(figure: Figure, axes, series: MeteogramSeries, tracked, *, report_mode: bool = False) -> None:
    timezone = series.times[0].tzinfo
    duration_days = (
        series.times[-1].timestamp() - series.times[0].timestamp()
    ) / 86400.0
    axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=1, tz=timezone))
    weekdays = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

    def day_label(value, _position):
        current = mdates.num2date(value, tz=timezone)
        return f"{current:%d.%m}\n{weekdays[current.weekday()]}"

    axes[-1].xaxis.set_major_formatter(plt.FuncFormatter(day_label))
    minor_hours = (6, 12, 18) if duration_days <= 8 else (12,)
    axes[-1].xaxis.set_minor_locator(mdates.HourLocator(byhour=minor_hours, tz=timezone))
    axes[-1].xaxis.set_minor_formatter(mdates.DateFormatter("%H", tz=timezone))
    axes[-1].tick_params(axis="x", which="major", labelsize=6.9, pad=5)
    axes[-1].tick_params(axis="x", which="minor", labelsize=5.8, pad=2)
    axes[-1].set_xlim(series.times[0], series.times[-1])
    for axis in axes[:-1]:
        axis.tick_params(axis="x", labelbottom=False)

    if report_mode:
        figure.subplots_adjust(left=0.078, right=0.895, top=0.955, bottom=0.115)
        return

    distance = grid_distance_km(series)
    grid = ""
    if series.grid_lat is not None and series.grid_lon is not None:
        grid = f" · расчётная точка {series.grid_lat:.3f},{series.grid_lon:.3f}"
        if distance is not None:
            grid += f" ({distance:.1f} км)"
    generated = datetime.now(dt_timezone.utc)
    footer1 = figure.text(
        0.063, 0.096,
        f"Запрошено {series.requested_lat:.4f},{series.requested_lon:.4f}{grid}",
        ha="left", va="bottom", fontsize=6.35, color=COLORS["muted"],
    )
    footer2 = figure.text(
        0.063, 0.074,
        f"Получено {series.retrieved_at_utc:%d.%m.%Y %H:%M} UTC · PNG {generated:%d.%m.%Y %H:%M} UTC · модельный прогноз, не наблюдение",
        ha="left", va="bottom", fontsize=6.35, color=COLORS["muted"],
    )
    explanation = (
        "T/Td/p — среднее; направление — круговое среднее; прочее — медиана; q25–q75/q10–q90; P — доля членов за исходный интервал"
        if series.source.ensemble
        else "непрерывные поля сглажены PCHIP; осадки показаны без сглаживания"
    )
    footer3 = figure.text(0.063, 0.052, explanation, ha="left", va="bottom", fontsize=6.15, color=COLORS["muted"])
    footer4 = figure.text(0.063, 0.030, "Пунктир — среднее за 24 ч; мин./макс. T — по доступным срокам местных суток.", ha="left", va="bottom", fontsize=5.95, color=COLORS["muted"])
    footer5 = figure.text(0.063, 0.008, "Красный ромб — диагностический порог: T ≤−20/≥+35 °C; RH ≥95%; осадки ≥5 мм/ч; ветер ≥10; порывы ≥14 м/с.", ha="left", va="bottom", fontsize=5.80, color=COLORS["muted"])
    tracked.extend(((footer1, 100), (footer2, 100), (footer3, 100), (footer4, 100), (footer5, 100)))
    figure.subplots_adjust(left=0.078, right=0.895, top=0.855, bottom=0.185)
'''


def main() -> None:
    pdf_path = Path("meteogram_pdf.py")
    pdf = pdf_path.read_text(encoding="utf-8")
    pdf = replace_once(pdf, "import textwrap\n", "import re\nimport textwrap\n", "pdf import re")
    pdf = replace_block(pdf, "def _overview_page", "def _detail_pages", OVERVIEW)
    pdf = replace_block(pdf, "def _daily_spec", "def _control_spec", DAILY_SPEC)
    pdf = replace_block(pdf, "def _control_spec", "def _draw_table", CONTROL_SPEC)
    pdf = replace_block(pdf, "def _draw_notes", "def _draw_summary_lines", DRAW_NOTES)
    pdf = replace_block(pdf, "def _draw_summary_lines", "def _summary_height", SUMMARY_DRAW)
    pdf = replace_block(pdf, "def _summary_height", "def _table_height", SUMMARY_HEIGHT)
    pdf = replace_block(pdf, "def _row_heights", "def _paginate_rows", ROW_HEIGHTS)
    pdf = replace_block(pdf, "def _notes_height", "def _is_ensemble", NOTES_HEIGHT)
    pdf = replace_block(pdf, "def _crop_image", "def _page", CROP_IMAGE + "\n\n" + HELPERS)
    pdf_path.write_text(pdf, encoding="utf-8")

    plot_path = Path("meteogram_plot.py")
    plot = plot_path.read_text(encoding="utf-8")
    plot = replace_once(
        plot,
        "def write_meteogram_png(\n    series: MeteogramSeries,\n    output_path: Path | None = None,\n) -> Path:\n    figure, _axes, _tracked = build_meteogram_figure(series)",
        "def write_meteogram_png(\n    series: MeteogramSeries,\n    output_path: Path | None = None,\n    *,\n    report_mode: bool = False,\n) -> Path:\n    figure, _axes, _tracked = build_meteogram_figure(series, report_mode=report_mode)",
        "write_meteogram_png report mode",
    )
    plot = replace_once(
        plot,
        "def build_meteogram_figure(\n    series: MeteogramSeries,\n) -> tuple[Figure, tuple, list[tuple[Artist, int]]]:",
        "def build_meteogram_figure(\n    series: MeteogramSeries,\n    *,\n    report_mode: bool = False,\n) -> tuple[Figure, tuple, list[tuple[Artist, int]]]:",
        "build_meteogram_figure report mode",
    )
    plot = replace_once(plot, "    _draw_header(figure, series, tracked)\n", "    if not report_mode:\n        _draw_header(figure, series, tracked)\n", "skip report header")
    plot = replace_once(plot, "    _finish_axes(figure, axes, series, tracked)\n", "    _finish_axes(figure, axes, series, tracked, report_mode=report_mode)\n", "report finish axes")
    plot_path.write_text(plot, encoding="utf-8")

    common_path = Path("meteogram_plot_common.py")
    common = common_path.read_text(encoding="utf-8")
    common = replace_block(common, "def _finish_axes", "def _line", FINISH_AXES)
    common_path.write_text(common, encoding="utf-8")

    report_path = Path("meteogram_report.py")
    report = report_path.read_text(encoding="utf-8")
    report = replace_once(
        report,
        '    point_label = _clean_text(getattr(series, "point_label", "Точка прогноза"))\n',
        '    point_label = _clean_text(getattr(series, "point_label", "Точка прогноза"))\n    point_label = re.sub(r"^(?:г\\.?\\s+)", "", point_label, flags=re.IGNORECASE)\n',
        "clean city prefix",
    )
    report = replace_once(
        report,
        "    cleanup = [docx_path]\n    if fmt == \"docx\":",
        "    cleanup = [docx_path]\n    report_chart = chart\n    if fmt == \"pdf\":\n        try:\n            from meteogram_plot import write_meteogram_png\n\n            report_chart = out_dir / f\"{data.filename_stem}.report.png\"\n            write_meteogram_png(series, report_chart, report_mode=True)\n            cleanup.append(report_chart)\n        except Exception:\n            report_chart = chart\n    if fmt == \"docx\":",
        "report chart generation",
    )
    report = replace_once(report, "        write_meteogram_pdf(data, chart, pdf_path)\n", "        write_meteogram_pdf(data, report_chart, pdf_path)\n", "use report chart")
    report_path.write_text(report, encoding="utf-8")

    docs_path = Path("docs/METEOGRAM.md")
    docs = docs_path.read_text(encoding="utf-8")
    note = (
        "\n- PDF использует компактную report-mode метеограмму без повторной шапки и технического футера; "
        "в таблицах одинаковый размер ансамбля выносится из строк, нулевые вероятности сильных осадков скрываются, "
        "а высота строк рассчитывается по фактическому числу строк текста.\n"
    )
    if "report-mode метеограмму" not in docs:
        docs = docs.rstrip() + "\n" + note
    docs_path.write_text(docs, encoding="utf-8")

    test_path = Path("tests/test_meteogram_pdf_readability.py")
    test_path.write_text(
        '''from __future__ import annotations\n\nimport unittest\nfrom types import SimpleNamespace\n\nimport meteogram_pdf as pdf\n\n\nclass MeteogramPdfReadabilityTests(unittest.TestCase):\n    def test_daily_table_hides_uniform_ensemble_column(self):\n        rows = [\n            SimpleNamespace(\n                day=__import__("datetime").date(2026, 8, 16),\n                weather="Осадки вероятны",\n                temperature="+15,0…+20,0 °C\\nq10-q90 +13,0…+22,0 °C",\n                precipitation="2,4 мм за 15 ч\\nсумма центрального ряда\\nP≥0,1 мм/3 ч: 92 % (47/51)\\nP≥1 мм/3 ч: 12 % (6/51)\\nP≥5 мм/3 ч: 0 % (0/51)",\n                wind="до 5,0 м/с", pressure="1000…1005 гПа",\n                ensemble="51/51 членов\\nустойчивый сигнал",\n            )\n            for _ in range(2)\n        ]\n        data = SimpleNamespace(daily_rows=rows)\n        headers, values, *_ = pdf._daily_spec(data, True)\n        self.assertNotIn("Ансамбль", headers)\n        self.assertTrue(all(len(row) == 6 for row in values))\n        precip = values[0][3]\n        self.assertNotIn("центрального ряда", precip)\n        self.assertNotIn("(47/51)", precip)\n        self.assertNotIn("P≥5", precip)\n        self.assertIn("P≥0,1/3 ч: 92 %", precip)\n        self.assertIn("P≥1/3 ч: 12 %", precip)\n        self.assertIn("q10–q90", values[0][2])\n\n    def test_multiline_rows_get_real_height(self):\n        height = pdf._row_heights([["a\\nb\\nc\\nd\\ne"]])[0]\n        self.assertGreaterEqual(height, 0.07)\n\n    def test_zero_median_is_not_worded_as_no_precip_when_probability_exists(self):\n        value = pdf._compact_precipitation(\n            "без существенных осадков\\nP≥0,1 мм/3 ч: 33 % (17/51)",\n            ensemble=True, control=True,\n        )\n        self.assertIn("медиана 0 мм", value)\n        self.assertIn("P≥0,1/3 ч: 33 %", value)\n        self.assertNotIn("без существенных", value)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
