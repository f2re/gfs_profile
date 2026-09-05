from __future__ import annotations

"""Independent platform configuration for Telegram, MAX and VK.

A broken optional platform must never prevent the shared runtime from serving
other configured platforms or the web/API.  Each platform therefore has its
own requested/ready/degraded state.
"""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

TRUE_VALUES = {"1", "true", "yes", "on", "да"}
FALSE_VALUES = {"0", "false", "no", "off", "нет"}
AUTO_VALUES = {"", "auto", "default"}
MAX_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


@dataclass(frozen=True)
class PlatformStatus:
    name: str
    requested: bool
    ready: bool
    state: str
    reason: str = ""

    @classmethod
    def off(cls, name: str, reason: str = "отключено") -> "PlatformStatus":
        return cls(name=name, requested=False, ready=False, state="off", reason=reason)

    @classmethod
    def active(cls, name: str) -> "PlatformStatus":
        return cls(name=name, requested=True, ready=True, state="ready")

    @classmethod
    def degraded(cls, name: str, reason: str) -> "PlatformStatus":
        return cls(name=name, requested=True, ready=False, state="degraded", reason=reason)

    def as_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "ready": self.ready,
            "state": self.state,
            "reason": self.reason,
        }


def _requested(name: str, token_var: str) -> tuple[bool, str | None]:
    raw = os.getenv(f"{name.upper()}_ENABLED", "auto").strip().lower()
    if raw in TRUE_VALUES:
        return True, None
    if raw in FALSE_VALUES:
        return False, None
    if raw in AUTO_VALUES:
        return bool(os.getenv(token_var, "").strip()), None
    return True, f"{name.upper()}_ENABLED должен быть 1/0/auto"


def _https(value: str, name: str, *, max_port_443: bool = False) -> str | None:
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        return f"{name} должен быть публичным HTTPS URL"
    if parsed.username or parsed.password:
        return f"{name} не должен содержать логин/пароль"
    if parsed.fragment:
        return f"{name} не должен содержать #fragment"
    if max_port_443:
        try:
            port = parsed.port
        except ValueError:
            return f"{name} содержит некорректный порт"
        if port not in (None, 443):
            return f"{name}: MAX Webhook поддерживает только HTTPS порт 443"
    return None


def telegram_status() -> PlatformStatus:
    requested, flag_error = _requested("telegram", "TELEGRAM_BOT_TOKEN")
    if not requested:
        return PlatformStatus.off("telegram")
    if flag_error:
        return PlatformStatus.degraded("telegram", flag_error)
    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        return PlatformStatus.degraded("telegram", "TELEGRAM_BOT_TOKEN не задан")
    return PlatformStatus.active("telegram")


def max_status() -> PlatformStatus:
    requested, flag_error = _requested("max", "MAX_BOT_TOKEN")
    if not requested:
        return PlatformStatus.off("max")
    if flag_error:
        return PlatformStatus.degraded("max", flag_error)
    if not os.getenv("MAX_BOT_TOKEN", "").strip():
        return PlatformStatus.degraded("max", "MAX_BOT_TOKEN не задан")
    url_error = _https(os.getenv("MAX_WEBHOOK_URL", ""), "MAX_WEBHOOK_URL", max_port_443=True)
    if url_error:
        return PlatformStatus.degraded("max", url_error)
    secret = os.getenv("MAX_WEBHOOK_SECRET", "").strip()
    if not MAX_SECRET_RE.fullmatch(secret):
        return PlatformStatus.degraded(
            "max",
            "MAX_WEBHOOK_SECRET не подготовлен: 5..256 символов A-Z/a-z/0-9/_/-",
        )
    return PlatformStatus.active("max")


def vk_status() -> PlatformStatus:
    requested, flag_error = _requested("vk", "VK_BOT_TOKEN")
    if not requested:
        return PlatformStatus.off("vk")
    if flag_error:
        return PlatformStatus.degraded("vk", flag_error)
    if not os.getenv("VK_BOT_TOKEN", "").strip():
        return PlatformStatus.degraded("vk", "VK_BOT_TOKEN не задан")
    try:
        group_id = int(os.getenv("VK_GROUP_ID", "").strip())
    except ValueError:
        return PlatformStatus.degraded("vk", "VK_GROUP_ID должен быть числом без знака минус")
    if group_id <= 0:
        return PlatformStatus.degraded("vk", "VK_GROUP_ID должен быть положительным")
    url_error = _https(os.getenv("VK_CALLBACK_URL", ""), "VK_CALLBACK_URL")
    if url_error:
        return PlatformStatus.degraded("vk", url_error)
    if not os.getenv("VK_CALLBACK_SECRET", "").strip():
        return PlatformStatus.degraded("vk", "VK_CALLBACK_SECRET не подготовлен")
    if not os.getenv("VK_CONFIRMATION_CODE", "").strip():
        return PlatformStatus.degraded("vk", "VK_CONFIRMATION_CODE не подготовлен")
    return PlatformStatus.active("vk")


def platform_statuses() -> dict[str, PlatformStatus]:
    return {
        "telegram": telegram_status(),
        "max": max_status(),
        "vk": vk_status(),
    }


def requested_platforms() -> tuple[str, ...]:
    return tuple(name for name, status in platform_statuses().items() if status.requested)
