from __future__ import annotations

from io import BytesIO
from pathlib import Path

from telegram import InputFile
from telegram.error import BadRequest


def _input_file(payload: bytes, filename: str) -> InputFile:
    stream = BytesIO(payload)
    stream.name = filename
    return InputFile(stream, filename=filename)


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
    if prefer_photo:
        try:
            await message.reply_photo(photo=_input_file(payload, filename), caption=caption)
            return
        except BadRequest as exc:
            if not _is_photo_dimensions_error(exc):
                raise
    await message.reply_document(document=_input_file(payload, filename), caption=caption)
