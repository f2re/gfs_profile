from __future__ import annotations

from telegram import BotCommand

BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand("start", "🚀 Старт и геолокация"),
    BotCommand("help", "❓ Помощь и примеры"),
    BotCommand("profile", "📈 Профиль GFS"),
    BotCommand("aero", "🧾 Аэродиаграмма"),
    BotCommand("skewt", "📉 Быстрая Skew-T"),
    BotCommand("windgram", "🟦 Срок×уровень V/T/RH"),
    BotCommand("cloudgram", "☁️ Облака, осадки, грозы"),
    BotCommand("map", "🗺️ Карта: PNG-серия/GIF"),
    BotCommand("cycle", "🕒 Последний цикл GFS"),
    BotCommand("status", "⚙️ Статус и кэш"),
    BotCommand("cancel", "✖️ Сброс выбора"),
)

BOT_COMMAND_LINES: tuple[str, ...] = tuple(f"/{command.command} — {command.description}" for command in BOT_COMMANDS)


async def register_bot_commands(application) -> None:
    """Register slash commands for Telegram's command menu."""

    await application.bot.set_my_commands(list(BOT_COMMANDS))
