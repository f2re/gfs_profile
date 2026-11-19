from __future__ import annotations

"""Public command descriptions shared by platform registration adapters."""

from feature_flags import weathernext3_enabled


COMMAND_SPECS: tuple[tuple[str, str], ...] = (
    ("start", "🏠 Главное меню"),
    ("help", "❓ Краткая инструкция"),
    ("profile", "📈 Вертикальный профиль"),
    ("route", "✈️ Профиль по маршруту"),
    ("aero", "🎈 Аэрограмма"),
    ("windgram", "🌬️ Высота × срок"),
    ("cloudgram", "☁️ Облака, осадки, грозы"),
    ("meteogram", "📊 Метеограмма и отчёт DOCX/PDF"),
    ("map", "🗺️ Карта, серия, анимация"),
    ("icing", "❄️ Обледенение: вода и слои"),
    ("wn3", "🌐 WeatherNext 3"),
    ("schedule", "🕒 Автоматическая отправка"),
    ("settings", "⚙️ Мои точки и параметры"),
    ("cycle", "🕒 Последний цикл GFS"),
    ("status", "⏳ Состояние бота"),
    ("cancel", "✖️ Сброс сценария"),
)


def public_command_specs(platform: str) -> tuple[tuple[str, str], ...]:
    if platform not in {"telegram", "max", "vk"}:
        raise ValueError(f"Неизвестная платформа: {platform}")
    return tuple(
        (name, description)
        for name, description in COMMAND_SPECS
        if (name != "wn3" or weathernext3_enabled())
        # /cycle currently belongs to the legacy Telegram handler only.
        and (name != "cycle" or platform == "telegram")
    )
