from __future__ import annotations

"""Synchronize and verify public command menus without sending chat messages."""

import argparse
import asyncio
import os
import time
from pathlib import Path

from messenger.commands import public_command_specs
from messenger.max import MaxApiClient
from messenger.platform_config import max_status, telegram_status
from register_telegram_commands import _load_env_file, _register


def register_max_commands(*, status_only: bool = False, client: MaxApiClient | None = None) -> None:
    expected = [{"name": name, "description": description}
                for name, description in public_command_specs("max")]
    client = client or MaxApiClient(os.environ["MAX_BOT_TOKEN"])
    if not status_only:
        client.set_commands(expected)
    for attempt in range(3):
        received = client.get_bot_info().get("commands")
        if isinstance(received, list):
            actual = [{"name": item.get("name"), "description": item.get("description")}
                      for item in received if isinstance(item, dict)]
            if actual == expected:
                return
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("MAX: проверка команд после обновления не пройдена")


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновление и проверка меню команд Telegram/MAX")
    parser.add_argument("--status", action="store_true", help="проверить без изменения команд")
    platform = parser.add_mutually_exclusive_group()
    platform.add_argument("--telegram", action="store_true", help="только Telegram")
    platform.add_argument("--max", action="store_true", dest="only_max", help="только MAX")
    args = parser.parse_args()
    _load_env_file(Path(__file__).resolve().parent / ".env")
    # Preserve the legacy standalone Telegram token alias.
    if not os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("BOT_TOKEN"):
        os.environ["TELEGRAM_BOT_TOKEN"] = os.environ["BOT_TOKEN"]
    failed = False
    statuses = ((telegram_status(),) if args.telegram else
                (max_status(),) if args.only_max else
                (telegram_status(), max_status()))
    for status in statuses:
        name = status.name.upper()
        if not status.requested:
            print(f"OK {name}: платформа отключена; команды пропущены")
            continue
        if not status.ready:
            print(f"ERROR {name}: {status.reason}")
            failed = True
            continue
        try:
            if status.name == "telegram":
                asyncio.run(_register(status_only=args.status))
            else:
                register_max_commands(status_only=args.status)
            print(f"OK {name}: меню команд проверено" if args.status
                  else f"OK {name}: меню команд обновлено и проверено")
        except Exception as exc:
            # Third-party exceptions can include an HTTP URL containing a
            # token. Diagnostics identify the platform and class, not secrets.
            print(f"ERROR {name}: меню команд не подтверждено ({type(exc).__name__})")
            failed = True
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
