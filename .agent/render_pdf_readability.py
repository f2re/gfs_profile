from __future__ import annotations

from pathlib import Path

from meteogram_fetch import fetch_meteogram
from meteogram_plot import write_meteogram_png
from meteogram_report import write_meteogram_report


def main() -> int:
    out = Path("ui_artifacts")
    out.mkdir(exist_ok=True)
    series = fetch_meteogram(
        "aifs_ens",
        "г Санкт-Петербург",
        59.9391,
        30.3159,
        3,
    )
    png = write_meteogram_png(series, out / "telegram_meteogram.png")
    result = write_meteogram_report(
        series,
        png,
        "pdf",
        output_dir=out,
        pdf_fallback_to_docx=False,
    )
    target = out / "aifs_ens_spb_3d.pdf"
    if result.path != target:
        target.write_bytes(result.path.read_bytes())
    if target.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError("visual smoke did not produce a PDF")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
