# 🌦️ Профиль атмосферы GFS 0.25

Telegram-бот и веб-интерфейс для модельных вертикальных и временных продуктов GFS 0.25 по точке. Это **модельная точка GFS**, не радиозонд, не метеостанция и не наблюдение.

## Что умеет

- 📍 Точка по координатам, городу или Telegram-геолокации.
- ⏱️ Сроки GFS до `+384 ч` для профильных продуктов.
- 🔄 Progress: публикация файла, загрузка GRIB2, чтение `cfgrib/eccodes`, построение PNG/CSV.
- ⚡ NOMADS GRIB Filter: скачивается маленький subset, а не глобальный GRIB.
- 💾 Файловый кэш по циклу, сроку, точке, набору уровней и продукту.
- 📈 `/profile` — вертикальный профиль T/Td/RH/ветер/изотермы.
- ✈️ `/route` — вертикальный профиль вдоль маршрута до 500 гПа: целевой шаг 25 км, ETA из скорости, единый объективный риск и разные представления `simple/pro`.
- 🧾 `/aero` и `/skewt` — аэрологические диаграммы Stüve/Emagram/Skew-T через MetPy.
- 🟦 `/windgram` — срок × уровень: ветер, температура или влажность, со стрелками направления ветра.
- ☁️ `/cloudgram` — облачность, осадки, явления, видимость, грозовой риск и общая опасность в режимах `pro` и `simple`.
- 🧩 Широкие матричные PNG отправляются документом, если Telegram отклоняет их как photo.
- 🗺️ `/map` — единая композитная карта GFS вокруг точки: осадки, облачность, гроза, ветер AT500, явления и видимость; одна PNG, серия PNG или анимация.
- 🎞️ `mode=gif` для `/map` при наличии `ffmpeg` отправляет silent H.264/MP4-анимацию через Telegram animation; GIF используется как fallback.
- 📊 В кадрах `/map`-анимации сверху есть дата UTC, срок, номер кадра и progress-bar.
- 🔐 `/admin` — статистика использования, пользователи, админы и CSV-отчёты.
- 📋 После настройки wizard показывает команду для копирования и повторного запуска.

## Быстрая установка

```bash
bash install_telegram_bot.sh
```

Автоматически:

```bash
TELEGRAM_BOT_TOKEN='<BOT_TOKEN>' TELEGRAM_ADMIN_IDS='<TELEGRAM_USER_ID>' bash install_telegram_bot.sh --yes
```

Обновление после `git pull`:

```bash
git pull
bash deploy_telegram_bot.sh --yes
```

Если нужно подтянуть системные пакеты, включая `ffmpeg`:

```bash
bash deploy_telegram_bot.sh --install-system-packages --yes
```

Проверка:

```bash
python runtime_check.py
python -m unittest discover -s tests
ffmpeg -version | head -n 1
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

## Telegram-команды

```text
start - 🚀 Старт и геолокация
help - ❓ Помощь и примеры
profile - 📈 Профиль GFS
route - ✈️ Профиль по маршруту
aero - 🧾 Аэродиаграмма
skewt - 📉 Быстрая Skew-T
windgram - 🟦 Срок×уровень V/T/RH
cloudgram - ☁️ Облака, осадки, грозы
map - 🗺️ Карта: PNG-серия/анимация
cycle - 🕒 Последний цикл GFS
status - ⚙️ Статус и кэш
admin - 🔐 Администрирование
cancel - ✖️ Сброс выбора
```

Регистрация команд:

```bash
cd /opt/gfs_profile
source .venv/bin/activate
python register_telegram_commands.py
```

`/clouds` работает как alias `/cloudgram`, но в меню регистрируется только `/cloudgram`.

## Wizard-flow

Команды `/route`, `/aero`, `/skewt`, `/windgram`, `/cloudgram` и `/map` без параметров запускают пошаговый выбор. Для `/map` доступны две подложки: `Полная` и `Базовая`. Для `/windgram` верхний уровень фиксирован `top=500`.

## Маршрутный профиль `/route`

Пошаговый запуск:

```text
/route
Москва -> Санкт-Петербург
```

Экспертный формат:

```text
/route Москва -> Санкт-Петербург +24 speed=300 mode=pro
/route 55.75 37.62 -> 59.94 30.31 +6 speed=450 mode=simple
```

Расчёт:

- маршрут строится по дуге большого круга;
- целевой пространственный шаг — 25 км;
- ETA точки вычисляется из пройденного расстояния и средней скорости;
- срок точки — `lead вылета + ETA`, приведённый к доступному сроку GFS;
- до 161 точки: шаг до 25 км сохраняется для маршрутов примерно до 4000 км;
- каждая точка привязывается к ближайшему узлу GFS;
- для Москва–Новосибирск используется более 100 расчётных точек.

Контракт риска:

- `simple` и `pro` получают одинаковые точки, сроки и модельные поля;
- `point_risk`, обледенение, болтанка, облачность, гроза, видимость и ВНГО не зависят от режима;
- оба режима используют одинаковые границы 12 карточек и одинаковую категорию риска каждой карточки;
- различается только графика: `simple` — символы и короткие подписи, `pro` — барбы, RH, красные изотермы и числовые параметры.

Подробности: [`docs/ROUTE_PROFILE.md`](docs/ROUTE_PROFILE.md) и [`docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md`](docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md).

## Admin

Админ-доступ задаётся числовыми Telegram user id в `.env`:

```text
TELEGRAM_ADMIN_IDS=123456789,987654321
TELEGRAM_ADMIN_DB=.cache_gfs/admin_stats.sqlite3
```

Основные команды:

```text
/admin
/admin stats 30
/admin recent 20
/admin users
/admin find @username
/admin add @username
/admin add 123456789
/admin report requests 30
/admin report users
```

Telegram Bot API не поддерживает глобальный поиск пользователей. `/admin find` ищет только среди пользователей, уже известных боту.

Статистика хранится в SQLite без внешней БД. Deploy сохраняет `.cache_gfs`, `.env`, `.install-state` и `.venv`.

## Важно

GFS — модельная сетка, не радиозонд и не наблюдение. `/route` не определяет юридическую или фактическую возможность полёта. Перед полётом обязательны актуальные METAR/TAF/SIGMET/GAMET, NOTAM, данные диспетчерских и решение командира.
