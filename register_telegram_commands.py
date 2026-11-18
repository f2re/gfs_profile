from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telegram import Bot

from messenger.commands import public_command_specs
from telegram_commands import synchronize_bot_commands, verify_bot_commands


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _token_from_env() -> str:
    project_dir = Path(__file__).resolve().parent
    _load_env_file(project_dir / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN или BOT_TOKEN либо положить TELEGRAM_BOT_TOKEN в .env")
    return token


async def _register(*, status_only: bool = False) -> None:
    async with Bot(_token_from_env()) as bot:
        if status_only:
            await verify_bot_commands(bot)
        else:
            await synchronize_bot_commands(bot, verify=True)


def main() -> None:
    asyncio.run(_register())
    print("Telegram: команды обновлены и проверены")
    for name, description in public_command_specs("telegram"):
        print(f"  /{name} — {description}")


if __name__ == "__main__":
    main()
