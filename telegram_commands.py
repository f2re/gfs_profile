from __future__ import annotations

from telegram import BotCommand

BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand("start", "🌦 Главное меню"),
    BotCommand("help", "❓ Краткая инструкция"),
    BotCommand("profile", "📈 Вертикальный профиль"),
    BotCommand("route", "✈️ Профиль по маршруту"),
    BotCommand("aero", "🧾 Аэрологическая диаграмма"),
    BotCommand("windgram", "🟦 Срок × уровень"),
    BotCommand("cloudgram", "☁️ Облака, осадки, грозы"),
    BotCommand("map", "🗺️ Карта, серия, анимация"),
    BotCommand("cycle", "🕒 Последний цикл GFS"),
    BotCommand("status", "⚙️ Доступность и кэш"),
    BotCommand("cancel", "✖ Сброс сценария"),
)

BOT_COMMAND_LINES: tuple[str, ...] = tuple(
    f"/{command.command} — {command.description}" for command in BOT_COMMANDS
)


async def register_bot_commands(application) -> None:
    await application.bot.set_my_commands(list(BOT_COMMANDS))
