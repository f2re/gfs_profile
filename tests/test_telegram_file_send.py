from __future__ import annotations

import struct
import unittest

from telegram_file_send import _is_telegram_photo_safe, _png_dimensions

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_header(width: int, height: int) -> bytes:
    return PNG_SIGNATURE + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"


class TelegramFileSendTest(unittest.TestCase):
    def test_png_dimensions_read_ihdr(self) -> None:
        self.assertEqual(_png_dimensions(png_header(1600, 900)), (1600, 900))

    def test_wide_or_oversized_photo_is_sent_as_document(self) -> None:
        self.assertFalse(_is_telegram_photo_safe(png_header(9700, 600)))
        self.assertFalse(_is_telegram_photo_safe(png_header(5000, 200)))

    def test_normal_png_can_be_sent_as_photo(self) -> None:
        self.assertTrue(_is_telegram_photo_safe(png_header(1600, 900)))


if __name__ == "__main__":
    unittest.main()
