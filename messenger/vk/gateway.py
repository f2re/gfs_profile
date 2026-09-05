from __future__ import annotations

import asyncio
import json
from pathlib import Path

from messenger.contracts import NormalizedEvent, PlatformMessage, UiKeyboard
from messenger.errors import PlatformPermanentError, PlatformTemporaryError

from .client import VkApiClient


def _keyboard_json(keyboard: UiKeyboard | None) -> str | None:
    if keyboard is None:
        return None
    buttons = []
    for row in keyboard.rows:
        rendered = []
        for button in row:
            if button.action == "callback":
                payload = json.dumps({"p": button.payload}, ensure_ascii=False, separators=(",", ":"))
                rendered.append({"action": {"type": "callback", "label": button.text, "payload": payload}, "color": "secondary"})
            elif button.action == "request_location":
                rendered.append({"action": {"type": "location", "payload": "{}"}, "color": "secondary"})
            elif button.action == "link":
                rendered.append({"action": {"type": "open_link", "label": button.text, "link": button.url}})
            elif button.action == "text":
                rendered.append({"action": {"type": "text", "label": button.text, "payload": "{}"}, "color": "secondary"})
        if rendered:
            buttons.append(rendered)
    if not buttons:
        return None
    return json.dumps({"one_time": False, "inline": True, "buttons": buttons}, ensure_ascii=False, separators=(",", ":"))


class VkGateway:
    platform = "vk"

    def __init__(self, client: VkApiClient) -> None:
        self.client = client

    async def send_text(self, chat_id: str, text: str, *, keyboard: UiKeyboard | None = None, parse_mode: str | None = None) -> PlatformMessage:
        message_id = await asyncio.to_thread(self.client.send_message, chat_id, str(text), keyboard=_keyboard_json(keyboard))
        return PlatformMessage(self.platform, chat_id, str(message_id))

    async def edit_text(self, chat_id: str, message_id: str, text: str, *, keyboard: UiKeyboard | None = None, parse_mode: str | None = None) -> PlatformMessage:
        await asyncio.to_thread(self.client.edit_message, chat_id, message_id, str(text), keyboard=_keyboard_json(keyboard))
        return PlatformMessage(self.platform, chat_id, str(message_id))

    async def send_image(self, chat_id: str, path: Path, *, caption: str = "") -> PlatformMessage:
        attachment = await asyncio.to_thread(self.client.upload_photo, chat_id, Path(path))
        message_id = await asyncio.to_thread(self.client.send_message, chat_id, str(caption), attachment=attachment)
        return PlatformMessage(self.platform, chat_id, str(message_id))

    async def send_file(self, chat_id: str, path: Path, *, caption: str = "", filename: str | None = None) -> PlatformMessage:
        attachment = await asyncio.to_thread(self.client.upload_document, chat_id, Path(path), filename or Path(path).name)
        message_id = await asyncio.to_thread(self.client.send_message, chat_id, str(caption), attachment=attachment)
        return PlatformMessage(self.platform, chat_id, str(message_id))

    async def send_animation(self, chat_id: str, path: Path, *, caption: str = "") -> PlatformMessage:
        path = Path(path)
        if path.suffix.lower() == ".mp4":
            try:
                attachment = await asyncio.to_thread(self.client.upload_video, chat_id, path, path.stem)
                message_id = await asyncio.to_thread(self.client.send_message, chat_id, str(caption), attachment=attachment)
                return PlatformMessage(self.platform, chat_id, str(message_id))
            except (PlatformPermanentError, PlatformTemporaryError, ValueError):
                # Some community tokens may not have video.save rights. Delivery
                # must still succeed as a document and must not affect other platforms.
                pass
        return await self.send_file(chat_id, path, caption=caption, filename=path.name)

    async def answer_callback(self, event: NormalizedEvent, *, text: str | None = None) -> None:
        if event.callback_id:
            await asyncio.to_thread(self.client.answer_event, event.callback_id, event.user_id, event.chat_id, text)
