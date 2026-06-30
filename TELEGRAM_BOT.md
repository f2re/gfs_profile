# Telegram bot for GFS profiles

Минимальный бот даёт вертикальный профиль GFS 0.25° по координатам, названию города или геолокации Telegram. Он работает как один Python-процесс через long polling и не требует собственного HTTP API, Redis, базы данных или очередей.

## Что делает

- принимает `/profile <город|lat lon> [+lead]`;
- принимает Telegram-геолокацию и предлагает сроки `+0/+3/+6/+12/+24/+48`;
- сам выбирает последний доступный цикл GFS `00/06/12/18Z`;
- при необходимости позволяет задать экспертный цикл: `/profile Москва run=20260630/06 +24`;
- привязывает координаты к ближайшему узлу сетки GFS 0.25°;
- загружает малый GRIB2-срез через NOMADS GRIB Filter;
- возвращает краткую сводку и CSV-профиль.

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.telegram.example .env
# заполнить TELEGRAM_BOT_TOKEN
set -a && source .env && set +a
python telegram_bot.py
```

## Команды

```text
/start
/help
/cycle
/profile Москва +24
/profile 55.75 37.62 +12
/profile Санкт-Петербург run=20260630/06 +48
```

## Переменные окружения

```text
TELEGRAM_BOT_TOKEN     токен Telegram-бота
DEFAULT_LEAD           срок по умолчанию, часы; стандартно 24
MAX_CONCURRENT_GFS     максимум одновременных загрузок/парсингов; стандартно 2
GFS_CACHE_DIR          файловый кэш GRIB2
GFS_CACHE_TTL_SECONDS  срок хранения GRIB2; стандартно 24 часа
GEOCODER_USER_AGENT    User-Agent для Nominatim fallback
GEOCODE_CACHE_DIR      файловый кэш геокодирования
```

## Ограничения

Это модельный профиль ближайшего узла GFS, не радиозонд и не локальная станция. Для горной местности и приземного слоя интерпретация изобарических уровней требует осторожности. Если NOMADS временно недоступен, бот сообщает ошибку, а не маскирует её старыми данными.

## Systemd

```ini
[Unit]
Description=GFS Profile Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/gfs_profile
EnvironmentFile=/opt/gfs_profile/.env
ExecStart=/opt/gfs_profile/.venv/bin/python telegram_bot.py
Restart=always
RestartSec=5
User=gfsbot
Group=gfsbot

[Install]
WantedBy=multi-user.target
```
