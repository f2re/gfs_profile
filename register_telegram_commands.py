from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telegram import Bot, BotCommandScopeAllPrivateChats, BotCommandScopeDefault

from telegram_commands import BOT_COMMAND_LINES, BOT_COMMANDS


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


async def _register() -> None:
    bot = Bot(_token_from_env())
    scopes = (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats())

    # Remove stale scope/language variants first. Telegram prefers a more
    # specific old list over the default one, which otherwise leaves users with
    # an outdated menu after deploy.
    for scope in scopes:
        for language in (None, "ru", "en"):
            await bot.delete_my_commands(scope=scope, language_code=language)

    for scope in scopes:
        await bot.set_my_commands(list(BOT_COMMANDS), scope=scope)
        await bot.set_my_commands(list(BOT_COMMANDS), scope=scope, language_code="ru")


def main() -> None:
    asyncio.run(_register())
    print("Telegram commands synchronized:")
    for line in BOT_COMMAND_LINES:
        print(f"  {line}")


if __name__ == "__main__":
    main()
