from __future__ import annotations

"""Strict deployment gate for the shared runtime's intentionally tolerant /ready."""

import argparse
from typing import Any, Callable

import requests


class ReadinessError(RuntimeError):
    pass


def validate_readiness(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("runtime") != "multi-messenger":
        raise ReadinessError("ответ не относится к multi-messenger runtime")
    states = payload.get("platform_status")
    if not isinstance(states, dict):
        raise ReadinessError("runtime не передал состояние платформ")
    for name in ("telegram", "max", "vk"):
        item = states.get(name)
        if not isinstance(item, dict) or not isinstance(item.get("requested"), bool):
            raise ReadinessError(f"runtime не передал корректное состояние {name.upper()}")
        if item["requested"]:
            if item.get("ready") is not True or item.get("state") != "ready":
                # Do not expose the free-form reason: a platform exception may
                # contain a URL with credentials. Detailed logs stay on server.
                raise ReadinessError(f"включённая платформа {name.upper()} не готова")
        elif item.get("state") != "off":
            raise ReadinessError(f"состояние отключённой платформы {name.upper()} противоречиво")
    if payload.get("status") != "ok":
        raise ReadinessError("runtime сообщил о неполной готовности")


def check_runtime_ready(url: str, *, timeout: float = 2.0, getter: Callable | None = None) -> None:
    try:
        response = (getter or requests.get)(url, timeout=timeout)
    except requests.RequestException as exc:
        raise ReadinessError(f"runtime недоступен ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise ReadinessError(f"runtime вернул HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise ReadinessError("runtime вернул некорректный JSON") from None
    validate_readiness(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка готовности всех включённых мессенджеров")
    parser.add_argument("url", help="URL /ready локального runtime")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    if not 0 < args.timeout <= 30:
        parser.error("timeout должен быть больше 0 и не больше 30 секунд")
    try:
        check_runtime_ready(args.url, timeout=args.timeout)
    except ReadinessError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("OK: runtime и все включённые мессенджеры готовы")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
