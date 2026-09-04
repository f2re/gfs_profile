from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from messenger.contracts import NormalizedEvent, PlatformMessage, UiKeyboard

from .client import MaxApiClient


def _keyboard_attachment(keyboard: UiKeyboard | None) -> dict[str, Any] | None:
    if keyboard is None:
        return None
    rows = []
    for row in keyboard.rows:
        rendered = []
        for button in row:
            if button.action == "callback":
                rendered.append({"type": "callback", "text": button.text, "payload": button.payload})
            elif button.action == "request_location":
                rendered.append({"type": "request_geo_location", "text": button.text})
            elif button.action == "link":
                rendered.append({"type": "link", "text": button.text, "url": button.url})
            elif button.action == "text":
                rendered.append({"type": "message", "text": button.text})
        if rendered:
            rows.append(rendered)
    if not rows:
        return None
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def _message_id(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    body = message.get("body") if isinstance(message, dict) and isinstance(message.get("body"), dict) else {}
    value = body.get("mid") or message.get("mid") or message.get("message_id")
    return str(value or "")


class MaxGateway:
    platform = "max"

    def __init__(self, client: MaxApiClient) -> None:
        self.client = client

    async def send_text(self, chat_id: str, text: str, *, keyboard: UiKeyboard | None = None, parse_mode: str | None = None) -> PlatformMessage:
        body: dict[str, Any] = {"text": str(text)[:4000]}
        if parse_mode in {"html", "markdown"}:
            body["format"] = parse_mode
        attachment = _keyboard_attachment(keyboard)
        if attachment:
            body["attachments"] = [attachment]
        payload = await asyncio.to_thread(self.client.send_message, chat_id, body)
        return PlatformMessage(self.platform, chat_id, _message_id(payload))

    async def edit_text(self, chat_id: str, message_id: str, text: str, *, keyboard: UiKeyboard | None = None, parse_mode: str | None = None) -> PlatformMessage:
        body: dict[str, Any] = {"text": str(text)[:4000]}
        if parse_mode in {"html", "markdown"}:
            body["format"] = parse_mode
        attachment = _keyboard_attachment(keyboard)
        if attachment:
            body["attachments"] = [attachment]
        await asyncio.to_thread(self.client.edit_message, message_id, body)
        return PlatformMessage(self.platform, chat_id, str(message_id))

    async def _send_uploaded(self, chat_id: str, path: Path, media_type: str, caption: str) -> PlatformMessage:
        payload = await asyncio.to_thread(self.client.upload, Path(path), media_type)
        body = {
            "text": str(caption)[:4000] if caption else None,
            "attachments": [{"type": media_type, "payload": payload}],
        }
        sent = await asyncio.to_thread(self.client.send_message, chat_id, {k: v for k, v in body.items() if v is not None})
        return PlatformMessage(self.platform, chat_id, _message_id(sent))

    async def send_image(self, chat_id: str, path: Path, *, caption: str = "") -> PlatformMessage:
        return await self._send_uploaded(chat_id, path, "image", caption)

    async def send_file(self, chat_id: str, path: Path, *, caption: str = "", filename: str | None = None) -> PlatformMessage:
        return await self._send_uploaded(chat_id, path, "file", caption)

    async def send_animation(self, chat_id: str, path: Path, *, caption: str = "") -> PlatformMessage:
        media_type = "video" if Path(path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} else "image"
        return await self._send_uploaded(chat_id, path, media_type, caption)

    async def answer_callback(self, event: NormalizedEvent, *, text: str | None = None) -> None:
        if not event.callback_id:
            return
        body = {"message": {"text": str(text)[:4000]}} if text else {}
        await asyncio.to_thread(self.client.answer_callback, event.callback_id, body)
