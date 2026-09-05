from __future__ import annotations

"""Offline configuration preflight for the production messenger runtime.

Runtime host/port errors are fatal.  Telegram/MAX/VK configuration is reported
per platform so a broken optional platform does not block the others.
"""

import argparse
import json
import os

from messenger.platform_config import PlatformStatus, platform_statuses

TRUE_VALUES = {"1", "true", "yes", "on", "да"}


class ConfigError(RuntimeError):
    pass


def _enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE_VALUES


def validate_environment(*, strict_platforms: set[str] | None = None) -> dict[str, object]:
    runtime = _enabled("MESSENGER_RUNTIME_ENABLED", "1")
    try:
        port = int(os.getenv("MESSENGER_RUNTIME_PORT", "8081"))
    except ValueError as exc:
        raise ConfigError("MESSENGER_RUNTIME_PORT должен быть числом") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("MESSENGER_RUNTIME_PORT должен быть в диапазоне 1..65535")

    statuses = platform_statuses()
    strict = {str(value).lower() for value in (strict_platforms or set())}
    failures = [status for name, status in statuses.items() if name in strict and status.requested and not status.ready]
    if failures:
        detail = "; ".join(f"{item.name.upper()}: {item.reason}" for item in failures)
        raise ConfigError(detail)

    return {
        "runtime_enabled": runtime,
        # Backwards-compatible booleans: True means the platform can be loaded.
        "telegram": statuses["telegram"].ready,
        "max": statuses["max"].ready,
        "vk": statuses["vk"].ready,
        "host": os.getenv("MESSENGER_RUNTIME_HOST", "127.0.0.1"),
        "port": port,
        "platform_status": {name: status.as_dict() for name, status in statuses.items()},
    }


def _format_status(status: PlatformStatus) -> str:
    if status.state == "off":
        return f"{status.name.upper()}=off"
    if status.ready:
        return f"{status.name.upper()}=ready"
    return f"{status.name.upper()}=degraded({status.reason})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка multi-messenger конфигурации")
    parser.add_argument("--strict", action="store_true", help="считать ошибкой любую включённую, но неготовую платформу")
    parser.add_argument("--strict-telegram", action="store_true")
    parser.add_argument("--strict-max", action="store_true")
    parser.add_argument("--strict-vk", action="store_true")
    parser.add_argument("--json", action="store_true", help="вывести machine-readable состояние")
    args = parser.parse_args()
    strict: set[str] = set()
    if args.strict:
        strict.update(("telegram", "max", "vk"))
    if args.strict_telegram:
        strict.add("telegram")
    if args.strict_max:
        strict.add("max")
    if args.strict_vk:
        strict.add("vk")

    try:
        state = validate_environment(strict_platforms=strict)
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 2

    if args.json:
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return 0

    statuses = platform_statuses()
    print(
        "messenger-config: ok · "
        f"runtime={'on' if state['runtime_enabled'] else 'legacy'} · "
        + " · ".join(_format_status(statuses[name]) for name in ("telegram", "max", "vk"))
    )
    for status in statuses.values():
        if status.requested and not status.ready:
            print(f"WARN {status.name.upper()}: {status.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
