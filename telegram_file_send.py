from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path

from telegram import InputFile
from telegram.error import BadRequest

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TELEGRAM_PHOTO_MAX_DIMENSION_SUM = 10_000
TELEGRAM_PHOTO_MAX_ASPECT_RATIO = 20.0


def _input_file(payload: bytes, filename: str) -> InputFile:
    stream = BytesIO(payload)
    stream.name = filename
    return InputFile(stream, filename=filename)


def _png_dimensions(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 24 or not payload.startswith(PNG_SIGNATURE) or payload[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", payload[16:24])
    return int(width), int(height)


def _is_telegram_photo_safe(payload: bytes) -> bool:
    dimensions = _png_dimensions(payload)
    if dimensions is None:
        return True
    width, height = dimensions
    if width <= 0 or height <= 0:
        return False
    if width + height > TELEGRAM_PHOTO_MAX_DIMENSION_SUM:
        return False
    if max(width, height) / min(width, height) > TELEGRAM_PHOTO_MAX_ASPECT_RATIO:
        return False
    return True


def _is_photo_dimensions_error(exc: BadRequest) -> bool:
    text = str(exc).lower()
    return "photo_invalid_dimensions" in text or "photo invalid dimensions" in text


async def reply_png_file(message, png_path: Path, *, caption: str, prefer_photo: bool = True) -> None:
    """Reply with a PNG as photo when possible, otherwise as a document.

    Wide matrix products can exceed Telegram Bot API photo dimension/aspect
    constraints even when the PNG itself is valid. Sending them as documents
    preserves the original file and avoids Photo_invalid_dimensions failures.
    """

    filename = png_path.name
    payload = png_path.read_bytes()
    if prefer_photo and _is_telegram_photo_safe(payload):
        try:
            await message.reply_photo(photo=_input_file(payload, filename), caption=caption)
            return
        except BadRequest as exc:
            if not _is_photo_dimensions_error(exc):
                raise
    await message.reply_document(document=_input_file(payload, filename), caption=caption)
