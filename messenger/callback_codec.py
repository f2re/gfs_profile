from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote


CALLBACK_VERSION = "v1"
COMMON_CALLBACK_LIMIT_BYTES = 64


class CallbackCodecError(ValueError):
    pass


@dataclass(frozen=True)
class CallbackData:
    scope: str
    action: str
    value: str | None = None


def _escape(value: str) -> str:
    if not value:
        raise CallbackCodecError("Callback segment must not be empty")
    return quote(value, safe="-_.~")


def encode_callback(scope: str, action: str, value: object | None = None) -> str:
    parts = [CALLBACK_VERSION, _escape(scope), _escape(action)]
    if value is not None:
        parts.append(_escape(str(value)))
    payload = "|".join(parts)
    if len(payload.encode("utf-8")) > COMMON_CALLBACK_LIMIT_BYTES:
        raise CallbackCodecError("Callback payload exceeds common 64-byte limit")
    return payload


def decode_callback(payload: str) -> CallbackData:
    if not isinstance(payload, str) or not payload:
        raise CallbackCodecError("Empty callback payload")
    if len(payload.encode("utf-8")) > COMMON_CALLBACK_LIMIT_BYTES:
        raise CallbackCodecError("Callback payload exceeds common 64-byte limit")
    parts = payload.split("|")
    if len(parts) not in {3, 4} or parts[0] != CALLBACK_VERSION:
        raise CallbackCodecError("Unsupported callback format")
    scope = unquote(parts[1])
    action = unquote(parts[2])
    value = unquote(parts[3]) if len(parts) == 4 else None
    if not scope or not action:
        raise CallbackCodecError("Invalid callback payload")
    return CallbackData(scope=scope, action=action, value=value)
