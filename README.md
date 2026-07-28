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
- ☁️ `/cloudgram` — облачность, осадки, видимость и грозовой риск.
- 🗺️ `/map` — одна карта, серия PNG или MP4-анимация.
- 🔐 `/admin` — статистика и CSV-отчёты.

## Методика и аудит параметров

Сводная таблица исходных полей GFS, расчётных формул, порогов и научных источников:

- [`docs/METEOROLOGICAL_PARAMETERS_AUDIT.md`](docs/METEOROLOGICAL_PARAMETERS_AUDIT.md)

В аудите отдельно перечислены обнаруженные P0/P1-ошибки единиц, выбора GRIB-полей, CAPE/CIN, видимости, ВНГО, грозовой диагностики, обледенения и болтанки.

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

`/start` простыми словами перечисляет все продукты и показывает inline-кнопки. Кнопка отправки геолокации появляется только при выборе точки и удаляется после ввода.

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
/cloudgram  облака, осадки и грозы
/map        карта, серия или анимация
/cycle      последний цикл
/status     доступность и кэш
/admin      администрирование
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
- уровень конденсации с высотой в метрах;
- облачные, ледяные и турбулентные слои;
- ветер, вертикальный сдвиг и годограф 0–8 км.

Подробно: [`docs/AERO_DIAGRAM.md`](docs/AERO_DIAGRAM.md).

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

## Admin

Исторические запросы `skewt` объединяются со статистикой продукта `aero`; отдельный пункт `skewt` больше не показывается.

```env
TELEGRAM_ADMIN_IDS=123456789,987654321
TELEGRAM_ADMIN_DB=.cache_gfs/admin_stats.sqlite3
```

## Важно

Сглаженная картинка не повышает физическое разрешение GFS. Маршрутный продукт не является разрешением на полёт; перед полётом обязательны актуальные METAR/TAF/SIGMET/GAMET, NOTAM и решение командира.
