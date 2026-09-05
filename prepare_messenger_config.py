from __future__ import annotations

"""Prepare secrets and VK confirmation code before messenger runtime restart.

The operator only has to provide platform credentials and public callback URLs.
Generated secrets and the VK confirmation code are persisted in the existing
.env without printing secret values to stdout.
"""

import argparse
import os
import secrets
from pathlib import Path

from messenger.vk import VkApiClient


class PrepareConfigError(RuntimeError):
    pass


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _unquote(value)
    return values


def set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.lstrip().startswith(prefix):
            if not replaced:
                output.append(f"{key}={value}")
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if output and output[-1] != "":
            output.append("")
        output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _value(values: dict[str, str], key: str) -> str:
    return os.getenv(key, "").strip() or values.get(key, "").strip()


def _random_secret() -> str:
    return secrets.token_urlsafe(32)


def _vk_confirmation_code(token: str, group_id: int, api_version: str) -> str:
    client = VkApiClient(token, api_version=api_version)
    payload = client.call("groups.getCallbackConfirmationCode", group_id=group_id)
    if not isinstance(payload, dict) or not payload.get("code"):
        raise PrepareConfigError("VK API не вернул confirmation code")
    return str(payload["code"])


def prepare_environment(path: Path) -> list[str]:
    path = Path(path)
    values = read_env_file(path)
    changed: list[str] = []

    max_token = _value(values, "MAX_BOT_TOKEN")
    max_url = _value(values, "MAX_WEBHOOK_URL")
    max_secret = _value(values, "MAX_WEBHOOK_SECRET")
    if max_token and max_url and not max_secret:
        set_env_value(path, "MAX_WEBHOOK_SECRET", _random_secret())
        values = read_env_file(path)
        changed.append("MAX_WEBHOOK_SECRET generated")

    vk_token = _value(values, "VK_BOT_TOKEN")
    vk_url = _value(values, "VK_CALLBACK_URL")
    vk_group_raw = _value(values, "VK_GROUP_ID")
    if vk_token or vk_url or vk_group_raw:
        if not vk_token:
            raise PrepareConfigError("VK_BOT_TOKEN не задан")
        if not vk_group_raw:
            raise PrepareConfigError("VK_GROUP_ID не задан")
        try:
            vk_group_id = int(vk_group_raw)
        except ValueError as exc:
            raise PrepareConfigError("VK_GROUP_ID должен быть числом без знака минус") from exc
        if vk_group_id <= 0:
            raise PrepareConfigError("VK_GROUP_ID должен быть положительным")
        if not vk_url:
            raise PrepareConfigError("VK_CALLBACK_URL не задан")

        vk_secret = _value(values, "VK_CALLBACK_SECRET")
        if not vk_secret:
            set_env_value(path, "VK_CALLBACK_SECRET", _random_secret())
            values = read_env_file(path)
            changed.append("VK_CALLBACK_SECRET generated")

        api_version = _value(values, "VK_API_VERSION") or "5.199"
        actual_code = _vk_confirmation_code(vk_token, vk_group_id, api_version)
        configured_code = _value(values, "VK_CONFIRMATION_CODE")
        if configured_code != actual_code:
            set_env_value(path, "VK_CONFIRMATION_CODE", actual_code)
            changed.append("VK_CONFIRMATION_CODE fetched" if not configured_code else "VK_CONFIRMATION_CODE refreshed")

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Подготовка MAX/VK secrets и VK confirmation code")
    parser.add_argument("--env-file", default=".env", help="путь к .env")
    args = parser.parse_args()
    path = Path(args.env_file)
    try:
        changed = prepare_environment(path)
    except PrepareConfigError as exc:
        print(f"ERROR: {exc}")
        return 2
    if changed:
        print("messenger-prepare: ok · " + "; ".join(changed))
    else:
        print("messenger-prepare: ok · изменений не требуется")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
