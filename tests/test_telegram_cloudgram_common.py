from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from geocode import GeoPoint
from messenger.contracts import CommonProductResult, ProductAttachment, ProgressEvent
from telegram_cloudgram import ParsedCloudgramRequest, run_cloudgram_product


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

    async def reply_document(self, document=None, filename=None, caption="", **kwargs):
        self.documents.append((document, filename, caption))


class TelegramCloudgramCommonTests(unittest.TestCase):
    def test_telegram_runner_uses_common_cloudgram_result(self) -> None:
        async def check() -> None:
            message = _Message()
            point = GeoPoint(55.75, 37.62, "Москва", "test")
            parsed = ParsedCloudgramRequest("Москва", None, 0, 72, 3, "simple")
            with tempfile.TemporaryDirectory() as tmp:
                png = Path(tmp) / "cloudgram.png"
                png.write_bytes(b"png")

                def builder(point_arg, lead_from, lead_to, step, mode, run, *, progress_callback=None, run_selector=None):
                    self.assertIs(point_arg, point)
                    self.assertEqual((lead_from, lead_to, step, mode), (0, 72, 3, "simple"))
                    self.assertIsNone(run)
                    self.assertIsNotNone(run_selector)
                    if progress_callback:
                        progress_callback(ProgressEvent("plot_start", "Строю PNG"))
                    return CommonProductResult(
                        "cloudgram",
                        "COMMON CLOUDGRAM SUMMARY",
                        [ProductAttachment("image", png, "cloudgram.png", "CLOUDGRAM PNG", "image/png")],
                        {"run_date": "20260905", "run_cycle": "00", "lead_to": 72},
                        repeat_command="/cloudgram 55.7500 37.6200 run=20260905/00 from=0 to=72 step=3 mode=simple",
                    )

                async def fake_reply_png_file(message_arg, path, *, caption="", prefer_photo=False):
                    self.assertIs(message_arg, message)
                    message.photos.append((path, caption, prefer_photo))

                with (
                    patch("telegram_cloudgram.build_cloudgram_product_result", side_effect=builder) as common_builder,
                    patch("telegram_cloudgram.reply_png_file", side_effect=fake_reply_png_file),
                ):
                    ok = await run_cloudgram_product(message, point, parsed, asyncio.Semaphore(1))

                self.assertTrue(ok)
                common_builder.assert_called_once()
                self.assertIn("COMMON CLOUDGRAM SUMMARY", message.status.edits)
                self.assertEqual(message.photos[-1][1], "CLOUDGRAM PNG")
                self.assertTrue(any("run=20260905/00" in text for text, _ in message.texts))
                self.assertFalse(png.exists())

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
