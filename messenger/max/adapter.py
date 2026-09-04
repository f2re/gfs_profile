from __future__ import annotations

from typing import Any

from messenger.contracts import Location, NormalizedEvent


def _command(text: str | None) -> str | None:
    if not text or not text.startswith("/"):
        return None
    token = text.split(maxsplit=1)[0][1:].split("@", 1)[0].strip().lower()
    return token or None


def _extract_location(message: dict[str, Any]) -> Location | None:
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    for attachment in body.get("attachments") or []:
        if not isinstance(attachment, dict) or attachment.get("type") != "location":
            continue
        payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else {}
        lat = payload.get("latitude", payload.get("lat"))
        lon = payload.get("longitude", payload.get("lon"))
        if lat is not None and lon is not None:
            return Location(float(lat), float(lon))
    location = body.get("location") if isinstance(body.get("location"), dict) else None
    if location:
        lat = location.get("latitude", location.get("lat"))
        lon = location.get("longitude", location.get("lon"))
        if lat is not None and lon is not None:
            return Location(float(lat), float(lon))
    return None


def _chat_route(update: dict[str, Any], message: dict[str, Any], user_id: str) -> str:
    chat_id = update.get("chat_id")
    recipient = message.get("recipient") if isinstance(message.get("recipient"), dict) else {}
    chat_id = chat_id or recipient.get("chat_id")
    chat_type = str(recipient.get("chat_type") or recipient.get("type") or "").lower()
    if chat_id is not None and chat_type in {"chat", "channel"}:
        return f"chat:{chat_id}"
    if chat_id is not None and not user_id:
        return f"chat:{chat_id}"
    return f"user:{user_id}"


def normalize_max_update(update: dict[str, Any]) -> NormalizedEvent | None:
    update_type = str(update.get("update_type") or "")
    timestamp = update.get("timestamp")
    ts = float(timestamp) / 1000.0 if timestamp is not None else None

    if update_type == "bot_started":
        user = update.get("user") if isinstance(update.get("user"), dict) else {}
        user_id = str(user.get("user_id") or "")
        chat_id = str(update.get("chat_id") or user_id)
        return NormalizedEvent(
            platform="max",
            raw_event_id=f"bot_started:{chat_id}:{timestamp}",
            event_type="START",
            user_id=user_id,
            chat_id=f"user:{user_id}" if user_id else f"chat:{chat_id}",
            timestamp=ts,
            metadata={"payload": update.get("payload")},
        )

    if update_type == "message_created":
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        user_id = str(sender.get("user_id") or update.get("user_id") or "")
        text = body.get("text")
        mid = body.get("mid") or message.get("message_id")
        location = _extract_location(message)
        return NormalizedEvent(
            platform="max",
            raw_event_id=str(mid or f"message_created:{user_id}:{timestamp}"),
            event_type="LOCATION" if location else ("COMMAND" if _command(text) else "TEXT"),
            user_id=user_id,
            chat_id=_chat_route(update, message, user_id),
            message_id=str(mid) if mid is not None else None,
            text=str(text) if text is not None else None,
            command=_command(str(text) if text is not None else None),
            location=location,
            timestamp=ts,
        )

    if update_type == "message_callback":
        callback = update.get("callback") if isinstance(update.get("callback"), dict) else {}
        user = update.get("user") if isinstance(update.get("user"), dict) else {}
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
        user_id = str(user.get("user_id") or callback.get("user_id") or sender.get("user_id") or "")
        callback_id = callback.get("callback_id")
        payload = callback.get("payload") if callback.get("payload") is not None else callback.get("data")
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        mid = body.get("mid")
        return NormalizedEvent(
            platform="max",
            raw_event_id=str(callback_id or f"message_callback:{user_id}:{timestamp}"),
            event_type="CALLBACK",
            user_id=user_id,
            chat_id=_chat_route(update, message, user_id),
            message_id=str(mid) if mid is not None else None,
            callback_payload=str(payload) if payload is not None else None,
            callback_id=str(callback_id) if callback_id is not None else None,
            timestamp=ts,
        )

    return None
