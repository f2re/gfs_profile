from __future__ import annotations

import json
from typing import Any

from messenger.contracts import Location, NormalizedEvent


def _command(text: str | None) -> str | None:
    if not text or not text.startswith("/"):
        return None
    token = text.split(maxsplit=1)[0][1:].split("@", 1)[0].strip().lower()
    return token or None


def _payload_value(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        value = payload.get("p", payload.get("payload"))
        return str(value) if value is not None else None
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return payload
        return _payload_value(decoded)
    return str(payload)


def normalize_vk_update(update: dict[str, Any]) -> NormalizedEvent | None:
    update_type = str(update.get("type") or "")
    group_id = update.get("group_id")
    event_id = update.get("event_id")

    if update_type == "message_new":
        obj = update.get("object") if isinstance(update.get("object"), dict) else {}
        message = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        user_id = str(message.get("from_id") or "")
        peer_id = str(message.get("peer_id") or user_id)
        text = str(message.get("text") or "")
        geo = message.get("geo") if isinstance(message.get("geo"), dict) else {}
        coordinates = geo.get("coordinates") if isinstance(geo.get("coordinates"), dict) else {}
        lat, lon = coordinates.get("latitude"), coordinates.get("longitude")
        location = Location(float(lat), float(lon)) if lat is not None and lon is not None else None
        message_id = message.get("id") or message.get("conversation_message_id")
        return NormalizedEvent(
            platform="vk",
            raw_event_id=str(event_id or f"message_new:{peer_id}:{message_id}"),
            event_type="LOCATION" if location else ("COMMAND" if _command(text) else "TEXT"),
            user_id=user_id,
            chat_id=peer_id,
            message_id=str(message_id) if message_id is not None else None,
            text=text,
            command=_command(text),
            location=location,
            timestamp=float(message.get("date")) if message.get("date") is not None else None,
            metadata={"group_id": group_id},
        )

    if update_type == "message_event":
        obj = update.get("object") if isinstance(update.get("object"), dict) else {}
        user_id = str(obj.get("user_id") or "")
        peer_id = str(obj.get("peer_id") or user_id)
        callback_id = obj.get("event_id") or event_id
        return NormalizedEvent(
            platform="vk",
            raw_event_id=str(callback_id or f"message_event:{peer_id}:{user_id}"),
            event_type="CALLBACK",
            user_id=user_id,
            chat_id=peer_id,
            message_id=str(obj.get("conversation_message_id") or "") or None,
            callback_payload=_payload_value(obj.get("payload")),
            callback_id=str(callback_id) if callback_id is not None else None,
            metadata={"group_id": group_id},
        )

    return None
