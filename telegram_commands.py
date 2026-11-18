from __future__ import annotations

import asyncio

from messenger.commands import COMMAND_SPECS, public_command_specs

from telegram import BotCommand

_ALL_BOT_COMMANDS = tuple(BotCommand(*spec) for spec in COMMAND_SPECS)

BOT_COMMANDS = tuple(BotCommand(*spec) for spec in public_command_specs("telegram"))

BOT_COMMAND_LINES: tuple[str, ...] = tuple(
    f"/{command.command} — {command.description}" for command in BOT_COMMANDS
)


async def synchronize_bot_commands(bot, *, verify: bool = False) -> None:
    from telegram import BotCommandScopeDefault, BotCommandScopeAllPrivateChats
    commands = [BotCommand(*spec) for spec in public_command_specs("telegram")]
    for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()):
        # Replace current lists before removing the old English override. A
        # network failure must not erase every usable menu during deployment.
        for language in ("", "ru"):
            if await bot.set_my_commands(commands, scope=scope, language_code=language) is False:
                raise RuntimeError("Telegram не подтвердил обновление команд")
        if await bot.delete_my_commands(scope=scope, language_code="en") is False:
            raise RuntimeError("Telegram не подтвердил удаление устаревшего меню en")
    if verify:
        await verify_bot_commands(bot)


async def verify_bot_commands(bot) -> None:
    from telegram import BotCommandScopeDefault, BotCommandScopeAllPrivateChats
    expected = public_command_specs("telegram")
    for attempt in range(3):
        matched = True
        for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()):
            for language in ("", "ru", "en"):
                current = await bot.get_my_commands(scope=scope, language_code=language)
                actual = tuple((command.command, command.description) for command in current)
                matched = matched and actual == (() if language == "en" else expected)
        if matched:
            return
        if attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError("Telegram: проверка команд после обновления не пройдена")


async def register_bot_commands(application) -> None:
    await synchronize_bot_commands(application.bot)


def install_command_registration(application) -> None:
    """Refresh command menus at startup without replacing existing schedule hooks."""
    previous = application.post_init

    async def post_init(app):
        try:
            await register_bot_commands(app)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Could not refresh Telegram command menus", exc_info=True)
        if previous is not None:
            await previous(app)

    application.post_init = post_init
