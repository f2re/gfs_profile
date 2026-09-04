from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from messenger.max import MaxApiClient
from messenger.vk import VkApiClient


MAX_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{5,256}$")
MAX_UPDATE_TYPES = ["bot_started", "message_created", "message_callback"]


class WebhookConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistrationResult:
    platform: str
    url: str
    detail: str


def _https_url(value: str, name: str) -> str:
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise WebhookConfigError(f"{name} должен быть публичным HTTPS URL")
    return value


def _probe_max(url: str, secret: str, timeout: float = 15.0) -> None:
    try:
        response = requests.post(
            url,
            json={"update_type": "__gfs_preflight__"},
            headers={"X-Max-Bot-Api-Secret": secret},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise WebhookConfigError(f"MAX webhook недоступен: {exc}") from exc
    if response.status_code != 200:
        raise WebhookConfigError(f"MAX webhook preflight: HTTP {response.status_code}: {response.text[:200]}")


def _probe_vk(url: str, group_id: int, secret: str, confirmation_code: str, timeout: float = 15.0) -> None:
    try:
        response = requests.post(
            url,
            json={"type": "confirmation", "group_id": group_id, "secret": secret},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise WebhookConfigError(f"VK callback URL недоступен: {exc}") from exc
    if response.status_code != 200 or response.text.strip() != confirmation_code:
        raise WebhookConfigError(
            f"VK confirmation preflight не пройден: HTTP {response.status_code}, ответ={response.text[:100]!r}"
        )


def register_max(*, probe: bool = True, client: MaxApiClient | None = None) -> RegistrationResult:
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise WebhookConfigError("MAX_BOT_TOKEN не задан")
    url = _https_url(os.getenv("MAX_WEBHOOK_URL", ""), "MAX_WEBHOOK_URL")
    secret = os.getenv("MAX_WEBHOOK_SECRET", "").strip()
    if not MAX_SECRET_RE.fullmatch(secret):
        raise WebhookConfigError("MAX_WEBHOOK_SECRET: 5..256 символов A-Z/a-z/0-9/_/-")
    if probe:
        _probe_max(url, secret)
    client = client or MaxApiClient(token)
    existing = client.list_subscriptions()
    # MAX documents POST /subscriptions as both create and update operation.
    client.subscribe(url, secret, MAX_UPDATE_TYPES)
    matched = any(str(item.get("url") or "") == url for item in existing)
    return RegistrationResult("max", url, "подписка обновлена" if matched else "подписка создана")


def _vk_confirmation_code(client: VkApiClient, group_id: int) -> str:
    payload = client.call("groups.getCallbackConfirmationCode", group_id=group_id)
    if not isinstance(payload, dict) or not payload.get("code"):
        raise WebhookConfigError("VK API не вернул confirmation code")
    return str(payload["code"])


def _vk_server_id(payload: Any, url: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or str(item.get("url") or "") != url:
            continue
        value = item.get("id", item.get("server_id"))
        if value is not None:
            return int(value)
    return None


def register_vk(*, probe: bool = True, client: VkApiClient | None = None) -> RegistrationResult:
    token = os.getenv("VK_BOT_TOKEN", "").strip()
    if not token:
        raise WebhookConfigError("VK_BOT_TOKEN не задан")
    try:
        group_id = int(os.getenv("VK_GROUP_ID", "").strip())
    except ValueError as exc:
        raise WebhookConfigError("VK_GROUP_ID должен быть числом") from exc
    if group_id <= 0:
        raise WebhookConfigError("VK_GROUP_ID должен быть положительным")
    url = _https_url(os.getenv("VK_CALLBACK_URL", ""), "VK_CALLBACK_URL")
    secret = os.getenv("VK_CALLBACK_SECRET", "").strip()
    if not secret:
        raise WebhookConfigError("VK_CALLBACK_SECRET не задан")
    configured_code = os.getenv("VK_CONFIRMATION_CODE", "").strip()
    if not configured_code:
        raise WebhookConfigError("VK_CONFIRMATION_CODE не задан")

    api_version = os.getenv("VK_API_VERSION", "5.199")
    client = client or VkApiClient(token, api_version=api_version)
    actual_code = _vk_confirmation_code(client, group_id)
    if configured_code != actual_code:
        raise WebhookConfigError("VK_CONFIRMATION_CODE не совпадает с groups.getCallbackConfirmationCode")
    if probe:
        _probe_vk(url, group_id, secret, configured_code)

    servers = client.call("groups.getCallbackServers", group_id=group_id)
    server_id = _vk_server_id(servers, url)
    detail = "callback server обновлён"
    if server_id is None:
        created = client.call(
            "groups.addCallbackServer",
            group_id=group_id,
            url=url,
            title="GFS Profile",
            secret_key=secret,
        )
        if not isinstance(created, dict) or created.get("server_id") is None:
            raise WebhookConfigError("VK API не вернул server_id после addCallbackServer")
        server_id = int(created["server_id"])
        detail = "callback server создан"

    client.call(
        "groups.setCallbackSettings",
        group_id=group_id,
        server_id=server_id,
        api_version=api_version,
        message_new=1,
        message_event=1,
    )
    return RegistrationResult("vk", url, f"{detail}; message_new/message_event включены")


def main() -> int:
    parser = argparse.ArgumentParser(description="Регистрация MAX/VK webhook для GFS Profile")
    parser.add_argument("--max", action="store_true", dest="only_max", help="настроить только MAX")
    parser.add_argument("--vk", action="store_true", dest="only_vk", help="настроить только VK")
    parser.add_argument("--no-probe", action="store_true", help="не проверять публичный URL перед API регистрацией")
    args = parser.parse_args()
    selected_max = args.only_max or not (args.only_max or args.only_vk)
    selected_vk = args.only_vk or not (args.only_max or args.only_vk)
    results: list[RegistrationResult] = []
    try:
        if selected_max and os.getenv("MAX_BOT_TOKEN", "").strip():
            results.append(register_max(probe=not args.no_probe))
        elif args.only_max:
            raise WebhookConfigError("MAX_BOT_TOKEN не задан")
        if selected_vk and os.getenv("VK_BOT_TOKEN", "").strip():
            results.append(register_vk(probe=not args.no_probe))
        elif args.only_vk:
            raise WebhookConfigError("VK_BOT_TOKEN не задан")
    except WebhookConfigError as exc:
        print(f"ERROR: {exc}")
        return 2
    if not results:
        print("MAX/VK токены не заданы; регистрировать нечего")
        return 0
    for result in results:
        print(f"{result.platform.upper()}: {result.detail} · {result.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
