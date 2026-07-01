from __future__ import annotations

from telegram import BotCommand

BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand("start", "старт, геолокация и краткая инструкция"),
    BotCommand("help", "помощь по командам и форматам запросов"),
    BotCommand("profile", "вертикальный профиль GFS по точке и сроку"),
    BotCommand("aero", "аэрологическая диаграмма Stüve/Emagram/Skew-T"),
    BotCommand("skewt", "быстрая Skew-T диаграмма"),
    BotCommand("windgram", "срок×уровень: ветер, температура или влажность"),
    BotCommand("cycle", "последний опубликованный цикл GFS"),
    BotCommand("status", "доступность GFS, лимиты и состояние кэша"),
    BotCommand("cancel", "сброс текущего выбора или wizard-сценария"),
)

BOT_COMMAND_LINES: tuple[str, ...] = tuple(f"/{command.command} — {command.description}" for command in BOT_COMMANDS)


async def register_bot_commands(application) -> None:
    """Register slash commands for Telegram's command menu."""

    await application.bot.set_my_commands(list(BOT_COMMANDS))
