# 🌦️ Профиль атмосферы GFS 0.25

Telegram-бот и веб-интерфейс для вертикальных, временных и маршрутных продуктов GFS 0.25. Это профессиональный модельный инструмент, а не наблюдение.

## Что умеет

- 📍 Точка по координатам, городу или Telegram-геолокации.
- 🇷🇺 DaData Suggestions — основной геокодер; локальный справочник и Nominatim — резерв.
- ⏱️ Сроки GFS до `+384 ч`.
- 🔄 Progress для загрузки GRIB2, чтения `cfgrib/eccodes` и построения продукции.
- ⚡ NOMADS GRIB Filter: скачивается subset, а не глобальный GRIB.
- 💾 Файловый кэш по циклу, сроку, точке, уровням и продукту.
- 📈 `/profile` — вертикальный профиль T/Td/RH/ветер/изотермы.
- ✈️ `/route` — разрез вдоль маршрута до 500 гПа.
- 🧾 `/aero` — единая аэрологическая диаграмма Skew-T log-P с годографом.
- 🟦 `/windgram` — срок × уровень: ветер, температура или влажность.
- ☁️ `/cloudgram` — облачность, осадки, видимость и конвективный потенциал.
- 🗺️ `/map` — одна карта, серия PNG или MP4-анимация.

## Методика и аудит параметров

- [`docs/METEOROLOGICAL_METHODS.md`](docs/METEOROLOGICAL_METHODS.md) — действующая реализация: точные поля GFS, формулы, единицы, пороги, тесты и ограничения.
- [`docs/METEOROLOGICAL_PARAMETERS_AUDIT.md`](docs/METEOROLOGICAL_PARAMETERS_AUDIT.md) — исходный аудит, на основании которого исправлены P0/P1-дефекты.

Критические изменения реализации: `VIS` всегда переводится из метров в километры; GRIB-поля выбираются по shortName/слою/stepType/интервалу; CAPE/CIN берутся с 180–0 гПа AGL; ВНГО переводится MSL→AGL; общие и конвективные облака/осадки не смешиваются; Aero и Route загружают изобарические гидрометеоры GFS.

Обледенение и болтанка явно называются **модельными прокси**, поскольку GFS без наблюдений, PIREP, радара и спутника не заменяет CIP/FIP и GTG/EDR.

## Быстрая установка

```bash
bash install_telegram_bot.sh
```

Установщик запросит `TELEGRAM_BOT_TOKEN` и `DADATA_API_KEY`. Для DaData Suggestions нужен только API-ключ; Secret Key не требуется.

Неинтерактивно:

```bash
TELEGRAM_BOT_TOKEN='<BOT_TOKEN>' \
DADATA_API_KEY='<DADATA_API_KEY>' \
TELEGRAM_ADMIN_IDS='<TELEGRAM_USER_ID>' \
bash install_telegram_bot.sh --yes
```

Обновление:

```bash
git checkout telegram-bot
git pull
sudo bash deploy_telegram_bot.sh --yes
```

Проверка:

```bash
python -m unittest discover -s tests
python runtime_check.py
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

## Telegram UX

`/start` простыми словами перечисляет все пользовательские продукты и показывает inline-кнопки. Кнопка отправки геолокации появляется только при выборе точки и удаляется после ввода.

Подробно: [`docs/TELEGRAM_UX_MESSAGES.md`](docs/TELEGRAM_UX_MESSAGES.md).

## Геокодирование

```text
координаты из строки
→ кэш DaData
→ DaData
→ локальный справочник
→ Nominatim fallback
```

```env
GEOCODER_PROVIDERS=dadata,local,nominatim
DADATA_API_KEY=
DADATA_SUGGEST_URL=https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address
```

Подробно: [`docs/DADATA_GEOCODER.md`](docs/DADATA_GEOCODER.md).

## Telegram-команды

```text
/start      главное меню
/help       краткая инструкция
/profile    вертикальный профиль
/route      профиль по маршруту
/aero       аэрологическая диаграмма с годографом
/windgram   срок × уровень
/cloudgram  облака, осадки и конвективный потенциал
/map        карта, серия или анимация
/cycle      последний цикл
/status     доступность и кэш
/cancel     сброс сценария
```

Команда `/skewt` и выбор нескольких видов аэрологических диаграмм удалены. `/aero` всегда строит один согласованный Skew-T log-P.

## Аэрологическая диаграмма `/aero`

```text
/aero Москва +24
/aero 59.939 30.316 run=20260714/00 +12
```

Продукт содержит:

- красную кривую температуры среды;
- зелёную точку росы;
- чёрную кривую частицы;
- синюю кривую насыщения надо льдом;
- CAPE/CIN и ключевые индексы;
- уровень конденсации и изотермы в Zg MSL;
- гидрометеорные облачные слои;
- модельный icing proxy по SLWC;
- модельный CAT proxy по вертикальному сдвигу и Ri;
- ветер и годограф 0–8 км.

Подробно:

- [`docs/AERO_DIAGRAM.md`](docs/AERO_DIAGRAM.md)
- [`docs/METEOROLOGICAL_METHODS.md`](docs/METEOROLOGICAL_METHODS.md)

## Маршрутный профиль `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
/route Москва -> Новосибирск +6 speed=300 step=25 mode=simple
```

Детализация:

```text
25 км  — максимум деталей
50 км  — баланс качества и времени
100 км — быстрый обзор длинного маршрута
```

Если сетка создаёт 60 и более точек, бот предупреждает о длительном расчёте и предлагает выбрать шаг.

`simple` и `pro` используют одинаковые исходные данные, `point_risk` и карточки. Сглаживание применяется только к display-сетке PNG.

Подробности:

- [`docs/ROUTE_PROFILE.md`](docs/ROUTE_PROFILE.md)
- [`docs/ROUTE_PROFILE_RENDERING.md`](docs/ROUTE_PROFILE_RENDERING.md)
- [`docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md`](docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md)
- [`docs/ROUTE_RISK_CONTRACT.md`](docs/ROUTE_RISK_CONTRACT.md)

## Скрытая команда администратора

Команда администратора не публикуется в пользовательском меню и BotFather-командах, но остаётся доступной по прямому вводу для `TELEGRAM_ADMIN_IDS`.

```env
TELEGRAM_ADMIN_IDS=123456789,987654321
TELEGRAM_ADMIN_DB=.cache_gfs/admin_stats.sqlite3
```

Исторические запросы `skewt` объединяются со статистикой продукта `aero`.

## Важно

Сглаженная картинка не повышает физическое разрешение GFS. Маршрутный продукт и локальные шкалы риска не являются разрешением на полёт; перед полётом обязательны актуальные METAR/TAF/SIGMET/GAMET, NOTAM и решение командира.
