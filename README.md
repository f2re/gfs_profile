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

## 🚀 Быстрая установка Telegram-бота

Запустите установщик из корня репозитория:

```bash
bash install_telegram_bot.sh
```

Скрипт сам:

- 🔎 покажет текущее состояние установки;
- 📦 поставит системные пакеты Python, если разрешить;
- 👤 создаст системного пользователя `gfsbot`;
- 📁 скопирует проект в `/opt/gfs_profile`;
- 🐍 создаст `.venv` и установит зависимости;
- 🔐 спросит `TELEGRAM_BOT_TOKEN`, если он не задан ранее;
- ⚙️ создаст `.env` и systemd-сервис;
- ▶️ включит автозапуск и запустит бота.

Проверка после установки:

```bash
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -f
```

Проверка состояния без переустановки:

```bash
bash install_telegram_bot.sh --status
```

Неразговорный режим для автоматизации:

```bash
TELEGRAM_BOT_TOKEN='123456:AA...' bash install_telegram_bot.sh --yes
```

Полезные опции:

```text
--install-dir DIR       каталог установки, по умолчанию /opt/gfs_profile
--service-name NAME     имя systemd-сервиса, по умолчанию gfs-profile-bot
--service-user USER     системный пользователь, по умолчанию gfsbot
--skip-apt              не ставить системные пакеты через apt
--no-start              создать сервис, но не запускать
--status                только показать состояние
```

## 🧪 Проверка ядра без Telegram

```bash
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24 --csv /tmp/profile.csv
```

Если `--date` и `--cycle` не заданы, используется последний доступный запуск GFS.

## 🧪 Тесты

```bash
python -m unittest discover -s tests
```

## 🤖 Команды Telegram

```text
/start
/cycle
/profile Москва +24
/profile 55.75 37.62 +12
/profile Санкт-Петербург run=20260630/06 +48
```

Что приходит в ответ:

1. Краткая метеосводка по ключевым уровням: 1000, 925, 850, 700, 500, 300 гПа.
2. PNG-график с логарифмической шкалой давления и ветровыми перьями.
3. CSV со всеми уровнями профиля.

## 🌐 Запуск веб-интерфейса вручную

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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

## ⚠️ Ограничения

- GFS 0.25° сглаживает рельеф и локальные эффекты.
- В горах нижние изобарические уровни могут быть ниже поверхности модели.
- Уровень 0 °C считается по доступным изобарическим уровням, а не по фактическому радиозонду.
- Геокодинг неоднозначных городов может вернуть несколько вариантов; в этом случае используйте координаты.
- NOMADS может быть временно недоступен или ещё не опубликовать последний цикл.

## 🧹 Кэш

- GRIB2-файлы сохраняются в `.cache_gfs/`.
- Повторный запрос той же точки и срока использует файл из кэша.
- Старые файлы удаляются по `GFS_CACHE_TTL_SECONDS`.
