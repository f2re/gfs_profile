from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from geocode import GeoPoint
from messenger.contracts import CommonProductResult, ProductAttachment, ProgressEvent
from telegram_aero import ParsedAeroRequest, run_aero_product


class _Status:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text: str, *args, **kwargs):
        self.edits.append(text)


class _Message:
    def __init__(self) -> None:
        self.status = _Status()
        self.photos = []
        self.documents = []
        self.texts = []
        self.from_user = SimpleNamespace(id=100)

    async def reply_text(self, text: str, *args, **kwargs):
        self.texts.append((text, kwargs))
        if text.startswith("⏳"):
            return self.status
        return SimpleNamespace()

    async def reply_photo(self, photo=None, caption="", **kwargs):
        self.photos.append((photo, caption))

    async def reply_document(self, document=None, caption="", **kwargs):
        self.documents.append((document, caption))

    async def reply_animation(self, animation=None, caption="", **kwargs):
        self.photos.append((animation, caption))


class TelegramAeroCommonTests(unittest.TestCase):
    def test_telegram_runner_uses_common_aero_result(self) -> None:
        async def check() -> None:
            message = _Message()
            point = GeoPoint(55.75, 37.62, "Москва", "test")
            parsed = ParsedAeroRequest("Москва", 24, None)
            with tempfile.TemporaryDirectory() as tmp:
                png = Path(tmp) / "aero.png"
                png.write_bytes(b"png")

                def builder(point_arg, lead, run, *, progress_callback=None):
                    self.assertIs(point_arg, point)
                    self.assertEqual(lead, 24)
                    self.assertIsNone(run)
                    if progress_callback:
                        progress_callback(ProgressEvent("plot_start", "Строю диаграмму"))
                    return CommonProductResult(
                        "aero",
                        "COMMON AERO SUMMARY",
                        [ProductAttachment("image", png, "aero.png", "AERO PNG", "image/png")],
                        {"run_date": "20260904", "run_cycle": "06", "lead": 24},
                        repeat_command="/aero 55.7500 37.6200 run=20260904/06 +24",
                    )

                with patch("telegram_aero.build_aero_product_result", side_effect=builder) as common_builder:
                    ok = await run_aero_product(message, point, parsed, asyncio.Semaphore(1))

                self.assertTrue(ok)
                common_builder.assert_called_once()
                self.assertIn("COMMON AERO SUMMARY", message.status.edits)
                self.assertEqual(message.photos[-1][1], "AERO PNG")
                self.assertTrue(any("run=20260904/06" in text for text, _ in message.texts))
                self.assertFalse(png.exists())

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
