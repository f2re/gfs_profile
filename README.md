# 🌦️ Профиль атмосферы GFS 0.25

Telegram-бот и веб-интерфейс для модельных вертикальных и временных продуктов GFS 0.25. Это **модельная точка GFS**, не радиозонд, не метеостанция и не наблюдение.

## Что умеет

- 📍 Точка по координатам, городу или Telegram-геолокации.
- ⏱️ Сроки GFS до `+384 ч` для профильных продуктов.
- 🔄 Progress: публикация файла, загрузка GRIB2, чтение `cfgrib/eccodes`, построение PNG/CSV.
- ⚡ NOMADS GRIB Filter: скачивается subset, а не глобальный GRIB.
- 💾 Файловый кэш по циклу, сроку, точке, уровням и продукту.
- 📈 `/profile` — вертикальный профиль T/Td/RH/ветер/изотермы.
- ✈️ `/route` — разрез вдоль маршрута до 500 гПа: ETA из скорости, сетка 25/50/100 км, единый риск и режимы `simple/pro`.
- 🧾 `/aero` и `/skewt` — аэрологические диаграммы.
- 🟦 `/windgram` — срок × уровень: ветер, температура или влажность.
- ☁️ `/cloudgram` — облачность, осадки, явления, видимость и грозовой риск.
- 🗺️ `/map` — одна PNG, серия PNG или MP4-анимация.
- 🔐 `/admin` — статистика использования и CSV-отчёты.

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

Проверка:

```bash
python runtime_check.py
python -m unittest discover -s tests
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

## Маршрутный профиль `/route`

Пошаговый запуск:

```text
/route
Москва -> Санкт-Петербург
```

Экспертный формат:

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
/route 55.75 37.62 -> 59.94 30.31 +6 speed=450 step=25 mode=simple
```

Параметр `step`:

```text
25 км  — максимальная детализация
50 км  — сбалансированный вариант
100 км — быстрый обзор длинного маршрута
```

Если сетка создаёт 60 и более точек, бот предупреждает о длительном расчёте и показывает оценку числа точек для 25/50/100 км. Длинный прямой запрос без явного `step=` переводится на экран выбора детализации.

Названия пунктов сохраняются как ввёл пользователь и используются в заголовке PNG, статусе, caption и repeat-команде. Геокодер получает координаты, но не заменяет название пункта координатами. Для координат выводится полная пара широта/долгота.

Расчёт:

- маршрут строится по дуге большого круга;
- ETA точки = расстояние / средняя скорость;
- срок точки = `lead вылета + ETA`;
- шаг выборки выбирается пользователем;
- каждая точка привязывается к ближайшему узлу GFS;
- до 161 расчётной точки.

Контракт риска:

- `simple` и `pro` получают одинаковые точки, сроки и модельные поля;
- `point_risk`, обледенение, болтанка, облачность, гроза, видимость и ВНГО не зависят от режима;
- различается только графика.

Подробности:

- [`docs/ROUTE_PROFILE.md`](docs/ROUTE_PROFILE.md)
- [`docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md`](docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md)
- [`docs/ROUTE_RISK_CONTRACT.md`](docs/ROUTE_RISK_CONTRACT.md)

## Admin

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

## Важно

GFS — модельная сетка, не радиозонд и не наблюдение. `/route` не определяет юридическую или фактическую возможность полёта. Перед полётом обязательны актуальные METAR/TAF/SIGMET/GAMET, NOTAM, данные диспетчерских и решение командира.
