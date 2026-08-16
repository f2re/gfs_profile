from __future__ import annotations

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
