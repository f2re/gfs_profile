from __future__ import annotations

from feature_flags import weathernext3_enabled

from telegram import BotCommand

_ALL_BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand("start", "🌦 Главное меню"),
    BotCommand("help", "❓ Краткая инструкция"),
    BotCommand("profile", "📈 Вертикальный профиль"),
    BotCommand("route", "✈️ Профиль по маршруту"),
    BotCommand("aero", "🧾 Аэрологическая диаграмма"),
    BotCommand("windgram", "🟦 Срок × уровень"),
    BotCommand("cloudgram", "☁️ Облака, осадки, грозы"),
    BotCommand("meteogram", "📊 Метеограмма и отчёт DOCX/PDF"),
    BotCommand("map", "🗺️ Карта, серия, анимация"),
    BotCommand("wn3", "🛰 WeatherNext 3"),
    BotCommand("schedule", "🕒 Автоматическая отправка"),
    BotCommand("settings", "⚙️ Мои точки и параметры"),
    BotCommand("cycle", "🕒 Последний цикл GFS"),
    BotCommand("status", "⚙️ Доступность и кэш"),
    BotCommand("cancel", "✖ Сброс сценария"),
)

BOT_COMMANDS = tuple(c for c in _ALL_BOT_COMMANDS if c.command != "wn3" or weathernext3_enabled())

BOT_COMMAND_LINES: tuple[str, ...] = tuple(
    f"/{command.command} — {command.description}" for command in BOT_COMMANDS
)


async def register_bot_commands(application) -> None:
    from telegram import BotCommandScopeDefault, BotCommandScopeAllPrivateChats
    commands = [c for c in _ALL_BOT_COMMANDS if c.command != "wn3" or weathernext3_enabled()]
    for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()):
        await application.bot.delete_my_commands(scope=scope, language_code="en")
        for language in ("", "ru"):
            await application.bot.set_my_commands(commands, scope=scope, language_code=language)


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
