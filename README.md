# 🌦️ Профиль атмосферы GFS 0.25

Инструмент для получения вертикального модельного профиля атмосферы по точке: веб-интерфейс + Telegram-бот.

Важно: это **модельная точка GFS**, а не радиозонд, не метеостанция и не локальный фактический прогноз.

## ✅ Что умеет

- 📍 Профиль по координатам, городу или Telegram-геолокации.
- 🛰️ Источник: NOMADS GRIB Filter, GFS 0.25°.
- 🧭 Привязка точки к ближайшему узлу сетки GFS.
- 🌡️ Температура, точка росы, влажность, геопотенциальная высота.
- 🌬️ Ветер: направление, скорость, U/V-компоненты.
- ❄️ Диагностика уровня 0 °C.
- 📈 PNG-график профиля: T/Td, влажность, скорость ветра, ветровые перья.
- 📄 CSV-профиль для дальнейшей обработки.
- 🧪 Unit-тесты и GitHub Actions.

## 🧱 Архитектура

Минимальная схема без лишней инфраструктуры:

```text
Telegram / Web UI
        ↓
gfs_core.py
        ↓
NOMADS GRIB Filter → GRIB2 → cfgrib/eccodes → pandas
        ↓
текстовая сводка / PNG / CSV
```

Нет Redis, Celery, БД, webhook-сервера и отдельного API для Telegram-бота. Бот работает одним Python-процессом через long polling.

## 📦 Установка: базовая

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Проверка тестов:

```bash
python -m unittest discover -s tests
```

## 🧪 Проверка ядра без Telegram

```bash
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24 --csv /tmp/profile.csv
```

Если `--date` и `--cycle` не заданы, используется последний доступный запуск GFS.

## 🤖 Установка Telegram-бота

### 1. Создать `.env`

```bash
cp .env.telegram.example .env
```

### 2. Заполнить токен

```bash
TELEGRAM_BOT_TOKEN=123456:AA...
```

### 3. Запустить

```bash
set -a && source .env && set +a
python telegram_bot.py
```

### 4. Проверить в Telegram

```text
/start
/cycle
/profile Москва +24
/profile 55.75 37.62 +12
/profile Санкт-Петербург run=20260630/06 +48
```

## 🌐 Запуск веб-интерфейса

```bash
uvicorn main:app --reload
```

Открыть:

```text
http://127.0.0.1:8000
```

## ⚙️ Переменные окружения

```text
TELEGRAM_BOT_TOKEN     токен Telegram-бота
DEFAULT_LEAD           срок прогноза по умолчанию, часы; обычно 24
MAX_CONCURRENT_GFS     максимум одновременных GFS-запросов; обычно 2
GFS_CACHE_DIR          каталог файлового кэша GRIB2
GFS_CACHE_TTL_SECONDS  срок хранения GRIB2; обычно 86400
GFS_REQUEST_TIMEOUT    timeout загрузки NOMADS, секунды
GEOCODER_USER_AGENT    User-Agent для Nominatim fallback
GEOCODE_CACHE_DIR      каталог кэша геокодирования
GEOCODE_TIMEOUT        timeout геокодера, секунды
```

## 🧭 API веб-приложения

```text
GET  /healthz
GET  /api/available-cycles?date=YYYYMMDD
GET  /api/available-leads?date=YYYYMMDD&cycle=00|06|12|18
GET  /api/profile?date=YYYYMMDD&cycle=00&lead_index=24&lat=55.75&lon=37.62
POST /api/profile/start?...       фоновый расчёт
GET  /api/profile/status?job_id=...
GET  /api/cache-info
```

## 📊 Что приходит в Telegram

1. Краткая метеосводка по ключевым уровням: 1000, 925, 850, 700, 500, 300 гПа.
2. PNG-график с логарифмической шкалой давления и ветровыми перьями.
3. CSV со всеми уровнями профиля.

## ⚠️ Ограничения

- GFS 0.25° сглаживает рельеф и локальные эффекты.
- В горах нижние изобарические уровни могут быть ниже поверхности модели.
- Уровень 0 °C считается по доступным изобарическим уровням, а не по фактическому радиозонду.
- Геокодинг неоднозначных городов может вернуть несколько вариантов; в этом случае используйте координаты.
- NOMADS может быть временно недоступен или ещё не опубликовать последний цикл.

## 🛠️ Systemd для Telegram-бота

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

## 🧹 Кэш

- GRIB2-файлы сохраняются в `.cache_gfs/`.
- Повторный запрос той же точки и срока использует файл из кэша.
- Старые файлы удаляются по `GFS_CACHE_TTL_SECONDS`.
