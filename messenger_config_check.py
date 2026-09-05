from __future__ import annotations

"""Offline configuration preflight for the production messenger runtime."""

import os
import re
from urllib.parse import urlparse

TRUE_VALUES = {"1", "true", "yes", "on", "да"}
MAX_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


class ConfigError(RuntimeError):
    pass


def _enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE_VALUES


def _https(name: str) -> str:
    value = os.getenv(name, "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError(f"{name} должен быть публичным HTTPS URL")
    return value


def validate_environment() -> dict[str, object]:
    runtime = _enabled("MESSENGER_RUNTIME_ENABLED", "1")
    telegram = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    if not telegram:
        raise ConfigError("TELEGRAM_BOT_TOKEN не задан")

    max_token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if max_token:
        _https("MAX_WEBHOOK_URL")
        secret = os.getenv("MAX_WEBHOOK_SECRET", "").strip()
        if not MAX_SECRET_RE.fullmatch(secret):
            raise ConfigError("MAX_WEBHOOK_SECRET: 5..256 символов A-Z/a-z/0-9/_/-")

    vk_token = os.getenv("VK_BOT_TOKEN", "").strip()
    if vk_token:
        _https("VK_CALLBACK_URL")
        try:
            group_id = int(os.getenv("VK_GROUP_ID", "").strip())
        except ValueError as exc:
            raise ConfigError("VK_GROUP_ID должен быть числом") from exc
        if group_id <= 0:
            raise ConfigError("VK_GROUP_ID должен быть положительным")
        if not os.getenv("VK_CALLBACK_SECRET", "").strip():
            raise ConfigError("VK_CALLBACK_SECRET не задан")
        if not os.getenv("VK_CONFIRMATION_CODE", "").strip():
            raise ConfigError("VK_CONFIRMATION_CODE не задан")

    try:
        port = int(os.getenv("MESSENGER_RUNTIME_PORT", "8081"))
    except ValueError as exc:
        raise ConfigError("MESSENGER_RUNTIME_PORT должен быть числом") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("MESSENGER_RUNTIME_PORT должен быть в диапазоне 1..65535")

    return {
        "runtime_enabled": runtime,
        "telegram": telegram,
        "max": bool(max_token),
        "vk": bool(vk_token),
        "host": os.getenv("MESSENGER_RUNTIME_HOST", "127.0.0.1"),
        "port": port,
    }


def main() -> int:
    try:
        state = validate_environment()
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(
        "messenger-config: ok · "
        f"runtime={'on' if state['runtime_enabled'] else 'telegram-only'} · "
        f"MAX={'on' if state['max'] else 'off'} · VK={'on' if state['vk'] else 'off'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
