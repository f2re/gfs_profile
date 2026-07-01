from __future__ import annotations

import asyncio
import os

from telegram import Bot

from telegram_commands import BOT_COMMAND_LINES, BOT_COMMANDS


def _token_from_env() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN или BOT_TOKEN")
    return token


async def _register() -> None:
    bot = Bot(_token_from_env())
    await bot.set_my_commands(list(BOT_COMMANDS))


def main() -> None:
    asyncio.run(_register())
    print("Telegram commands registered:")
    for line in BOT_COMMAND_LINES:
        print(f"  {line}")


if __name__ == "__main__":
    main()
