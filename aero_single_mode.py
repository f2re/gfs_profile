from __future__ import annotations

"""Expose one aerological product and remove historical diagram aliases."""

from typing import Any

from telegram.ext import CommandHandler


def _human_home_text() -> str:
    return (
        "🌦 Метеобот GFS\n\n"
        "Прогноз атмосферы по точке или маршруту.\n\n"
        "📈 /profile — вертикальный профиль\n"
        "✈️ /route — профиль вдоль маршрута\n"
        "🧾 /aero — аэродиаграмма с годографом\n"
        "🟦 /windgram — ветер, температура и влажность\n"
        "☁️ /cloudgram — облака, осадки и грозы\n"
        "📊 /meteogram — прогноз по времени и ансамбль\n"
        "🗺️ /map — карта, серия или анимация\n"
        "🕒 /schedule — автоматическая отправка\n"
        "🕒 /cycle — последний цикл GFS\n"
        "⚙️ /status — данные и кэш\n"
        "✖ /cancel — сброс выбора\n\n"
        "Выберите продукт."
    )


def _human_help_text() -> str:
    return (
        "Как пользоваться\n\n"
        "1. Выберите продукт.\n"
        "2. Укажите город, координаты или геолокацию.\n"
        "3. Выберите срок и параметры.\n\n"
        "Примеры:\n"
        "<code>/profile Москва +24</code>\n"
        "<code>/route Москва -&gt; Санкт-Петербург +6</code>\n"
        "<code>/aero Москва +24</code>\n"
        "<code>/cloudgram Москва to=72 mode=simple</code>\n"
        "<code>/meteogram Москва ensemble=gefs days=5 format=pdf</code>\n"
        "<code>/map Москва from=0 to=24 step=3 mode=gif</code>\n"
        "<code>/schedule</code> — менеджер автоматических отправок"
    )


def install(namespace: dict[str, Any]) -> None:
    import telegram_concise_ux as ux

    if namespace.get("_AERO_SINGLE_MODE_INSTALLED"):
        return

    original_params_text = ux.params_text

    def params_text(state: dict[str, object]) -> str:
        if str(state.get("product", "")) != "aero":
            return original_params_text(state)
        point = ux._point_line(state)
        command = __import__("telegram_product_wizard").copy_command(state)
        command_line = f"\n<code>{command}</code>" if command else ""
        return (
            "🧾 Аэрологическая диаграмма\n"
            f"{point}\n"
            f"срок +{int(state.get('lead', 24))} ч · Skew-T log-P · годограф"
            f"{command_line}"
        )

    ux.home_text = _human_home_text
    ux.help_text = _human_help_text
    ux.params_text = params_text
    namespace["home_text"] = _human_home_text
    namespace["help_text"] = _human_help_text
    namespace["params_text"] = params_text
    namespace["_AERO_SINGLE_MODE_INSTALLED"] = True


def configure_application(application) -> None:
    """Remove /skewt handler after the embedded core builds the app."""

    for group, handlers in list(application.handlers.items()):
        for handler in list(handlers):
            commands = set(getattr(handler, "commands", ()) or ())
            if isinstance(handler, CommandHandler) and "skewt" in commands:
                application.remove_handler(handler, group=group)
