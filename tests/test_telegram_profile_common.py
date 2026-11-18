from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from messenger.contracts import CommonProductResult, ProductAttachment
import telegram_profile_common


class FakeStatus:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return self


class FakeMessage:
    def __init__(self):
        self.status = FakeStatus()
        self.texts = []
        self.photos = []
        self.documents = []
        self.animations = []
        self._first_text = True

    async def reply_text(self, text, **kwargs):
        if self._first_text:
            self._first_text = False
            self.initial = (text, kwargs)
            return self.status
        self.texts.append((text, kwargs))
        return self.status

    async def reply_photo(self, **kwargs):
        self.photos.append(kwargs)

    async def reply_document(self, **kwargs):
        self.documents.append(kwargs)

    async def reply_animation(self, **kwargs):
        self.animations.append(kwargs)


class TelegramProfileCommonTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_routes_telegram_profile_through_common_result(self):
        from geocode import GeoPoint

        point = GeoPoint(55.75, 37.62, "Москва", "test")
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "profile.png"
            csv = Path(tmp) / "profile.csv"
            png.write_bytes(b"png")
            csv.write_text("p_hPa\n1000\n", encoding="utf-8")
            result = CommonProductResult(
                product="profile",
                summary="plain summary",
                attachments=[
                    ProductAttachment("image", png, png.name, "PNG caption", "image/png"),
                    ProductAttachment("file", csv, csv.name, "CSV caption", "text/csv"),
                ],
                metadata={
                    "summary_html": "<b>GFS 0.25</b> profile",
                    "run_date": "20260904",
                    "run_cycle": "06",
                },
                repeat_command="/profile 55.7500 37.6200 +24",
            )
            namespace = {
                "GFS_SEMAPHORE": asyncio.Semaphore(1),
                "_profile_repeat_message": lambda point, lead, run: f"📋 <code>{run.date}/{run.cycle} +{lead}</code>",
            }
            telegram_profile_common.install(namespace)
            message = FakeMessage()
            with patch.object(telegram_profile_common, "build_profile_product", return_value=result) as builder, patch.object(
                telegram_profile_common, "cleanup_product_result"
            ) as cleanup:
                success = await namespace["run_profile"](message, point, 24, None)

            self.assertTrue(success)
            builder.assert_called_once()
            cleanup.assert_called_once_with(result)
            self.assertTrue(message.status.edits)
            final_text, final_kwargs = message.status.edits[-1]
            self.assertEqual(final_text, "<b>GFS 0.25</b> profile")
            self.assertEqual(str(final_kwargs.get("parse_mode")), "HTML")
            self.assertEqual(len(message.photos), 1)
            self.assertEqual(len(message.documents), 1)
            self.assertTrue(any("20260904/06 +24" in text for text, _ in message.texts))


if __name__ == "__main__":
    unittest.main()
