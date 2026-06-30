# 🤖 Telegram-бот GFS

Бот строит вертикальный профиль атмосферы GFS 0.25° по городу, координатам или Telegram-геолокации.

Схема работы: точка → доступный цикл GFS → малый GRIB2-срез → сводка, PNG и CSV.

## ✅ Что возвращает

- 🌡️ Температура и точка росы по изобарическим уровням.
- 💧 Относительная влажность.
- 🌬️ Ветер: направление, скорость, U/V-компоненты.
- ❄️ Уровень 0 °C или статус, если он вне профиля.
- 📈 PNG: T/Td, влажность, скорость ветра, ветровые перья.
- 📄 CSV со всеми уровнями.

## ⚠️ Важно

Это модельный профиль ближайшего узла GFS, не радиозонд. В горах, у моря, в городе и в приземном слое интерпретировать аккуратно.

## 📦 Установка

### 1. Python-окружение

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Файл настроек

```bash
cp .env.telegram.example .env
```

### 3. Минимальный `.env`

```text
TELEGRAM_BOT_TOKEN=123456:AA...
```

### 4. Рекомендуемые параметры

```text
DEFAULT_LEAD=24
MAX_CONCURRENT_GFS=2
GFS_CACHE_DIR=.cache_gfs
GFS_CACHE_TTL_SECONDS=86400
GFS_REQUEST_TIMEOUT=35
GEOCODER_USER_AGENT=gfs-profile-telegram-bot/0.1
GEOCODE_CACHE_DIR=.cache_gfs/geocode
GEOCODE_TIMEOUT=12
```

### 5. Запуск

```bash
set -a && source .env && set +a
python telegram_bot.py
```

## 🧪 Проверка

Команды в Telegram:

```text
/start
/cycle
/profile Москва +24
/profile 55.75 37.62 +12
/profile Санкт-Петербург run=20260630/06 +48
```

Проверка ядра без Telegram:

```bash
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24 --csv /tmp/profile.csv
```

Тесты:

```bash
python -m unittest discover -s tests
```

## 🧭 Синтаксис `/profile`

```text
/profile Москва +24
/profile 59.93 30.31 +12
/profile Москва run=20260630/06 +24
```

Telegram-геолокация:

```text
/start → 📍 Отправить геолокацию → выбрать срок +0/+3/+6/+12/+24/+48
```

## 📊 Как читать ответ

```text
Запуск         цикл модели GFS, UTC
Срок           заблаговременность прогноза, часы
Действительно  срок, на который рассчитан профиль, UTC
Узел GFS       ближайшая модельная точка 0.25°
Уровень 0 °C   интерполированная нулевая изотерма
Макс. ветер    максимум скорости ветра в профиле
```

## 🛠️ Systemd

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

## 🔎 Типовые ошибки

```text
Нужно задать TELEGRAM_BOT_TOKEN
```
Токен не загружен из `.env`.

```text
Для указанной даты/цикла данные GFS недоступны
```
Цикл ещё не опубликован или указан неверно.

```text
NOMADS вернул HTML вместо GRIB2
```
NOMADS не отдал GRIB2. Обычно это временная недоступность, неверный путь или слишком ранний цикл.

```text
Город или место не найдено
```
Используйте координаты или Telegram-геолокацию.
