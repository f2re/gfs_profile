# 🌦️ Профиль атмосферы GFS 0.25

Telegram-бот, MAX/VK messenger runtime и веб-интерфейс для вертикальных, временных и маршрутных продуктов GFS 0.25. Это профессиональный модельный инструмент, а не наблюдение.

## Что умеет

- 📍 Точка по координатам, городу или геолокации мессенджера.
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
- 📊 `/meteogram` — временная метеограмма модели/ансамбля с PNG/DOCX/PDF.
- 🗺️ `/map` — одна карта, серия PNG или MP4-анимация.
- 🕒 `/schedule` — автоматическая отправка продукции.
- ⚙️ `/settings` — основная точка, сохранённые параметры и быстрые действия.

Telegram поддерживает весь текущий набор продуктов. В общем Telegram+MAX+VK messenger service уже вынесены `/profile`, `/aero`, `/windgram` и `/cloudgram`; следующие продукты подключаются по одному vertical slice без дублирования метеорологической логики.

## Документация

- [`docs/METEOROLOGICAL_METHODS.md`](docs/METEOROLOGICAL_METHODS.md) — поля GFS, формулы, единицы, пороги и ограничения.
- [`docs/METEOROLOGICAL_PARAMETERS_AUDIT.md`](docs/METEOROLOGICAL_PARAMETERS_AUDIT.md) — аудит метеорологической корректности.
- [`docs/METEOGRAM.md`](docs/METEOGRAM.md) — модели, ансамбли и компоновка `/meteogram`.
- [`docs/AUTO_UPDATE.md`](docs/AUTO_UPDATE.md) — автообновление `telegram-bot` и rollback.
- [`docs/TELEGRAM_PERSONAL_UX.md`](docs/TELEGRAM_PERSONAL_UX.md) — точки, preferences и recipes Telegram.
- [`docs/MESSENGER_RUNTIME.md`](docs/MESSENGER_RUNTIME.md) — единый runtime Telegram+MAX+VK.
- [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md) — создание и настройка MAX/VK: что получить на платформах и что вставить в `.env`.
- [`docs/MESSENGER_AERO_SERVICE.md`](docs/MESSENGER_AERO_SERVICE.md) — общий `/aero`.
- [`docs/MESSENGER_WINDGRAM_SERVICE.md`](docs/MESSENGER_WINDGRAM_SERVICE.md) — общий `/windgram`.
- [`docs/MESSENGER_CLOUDGRAM_SERVICE.md`](docs/MESSENGER_CLOUDGRAM_SERVICE.md) — общий `/cloudgram`.

Критические правила: `VIS` переводится из метров в километры; GRIB-поля выбираются по shortName/слою/stepType/интервалу; CAPE/CIN берутся с 180–0 гПа AGL; `HGT cloud ceiling` используется как нативная высота AGL, а 20 000 м означает «потолка нет»; общие и конвективные облака/осадки не смешиваются. Обледенение и болтанка обозначаются как **модельные прокси**.

## Быстрая установка

```bash
bash install_telegram_bot.sh
```

Установщик запросит `TELEGRAM_BOT_TOKEN` и `DADATA_API_KEY` при включённой DaData.

Неинтерактивно:

```bash
TELEGRAM_BOT_TOKEN='<BOT_TOKEN>' \
DADATA_API_KEY='<DADATA_API_KEY>' \
TELEGRAM_ADMIN_IDS='<TELEGRAM_USER_ID>' \
bash install_telegram_bot.sh --yes
```

### Подключение MAX/VK после базовой установки

Сначала создайте бота/сообщество по [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md), настройте публичный HTTPS reverse proxy, затем:

```bash
sudo bash setup_messenger_bots.sh --max
sudo bash setup_messenger_bots.sh --vk
# или обе платформы:
sudo bash setup_messenger_bots.sh --max --vk
```

Мастер требует только:

```text
MAX: token + public HTTPS URL
VK: community token + positive group id + public HTTPS URL
```

MAX/VK secrets генерируются автоматически; VK confirmation code получается через VK API. После deploy мастер проверяет фактическую MAX subscription и VK Callback API registration.

Проверка:

```bash
curl -fsS http://127.0.0.1:8081/ready
sudo bash setup_messenger_bots.sh --status
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

## Обновление

```bash
git checkout telegram-bot
git pull --ff-only
sudo bash deploy_telegram_bot.sh --yes
```

Автообновление:

```bash
sudo bash install_auto_update.sh --yes
sudo bash install_auto_update.sh --status
```

Штатный deploy сохраняет `.env`, `.install-state`, `.venv`, `.cache_gfs` и basemap cache.

## Telegram UX

`/start` показывает стабильное меню, основную точку и до двух быстрых действий. Reply-кнопка геолокации появляется только при выборе точки. Preferences/recipes переживают restart и deploy.

Карта нового пользователя по умолчанию — анимация `+0…+48 ч`, шаг 3 ч. `/map Москва +24` означает одну карту.

## MAX/VK parity

Одинаковые common services уже используются для:

```text
/profile
/aero
/windgram
/cloudgram
```

Для MAX/VK работают город/координаты, неоднозначный город, native location, direct command, callback-flow, редактируемый progress и saved recipes. Repeat не сохраняет старый `run/cycle`.

`/windgram` default: ветер, `+0…+120 ч`, шаг 6 ч, до 500 гПа.

`/cloudgram` default: `Подробно`, `+0…+72 ч`, шаг 3 ч. Доступны `Подробно/Кратко`, `+24/+48/+72/+120`, шаг `3/6`.

Следующий общий vertical slice — `/map`.

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
/start       главное меню
/help        краткая инструкция
/profile     вертикальный профиль
/route       профиль по маршруту
/aero        аэрологическая диаграмма с годографом
/windgram    срок × уровень
/cloudgram   облака, осадки и конвективный потенциал
/meteogram   временной прогноз модели или ансамбля
/map         карта, серия или анимация
/schedule    автоматическая отправка
/settings    точки и сохранённые параметры
/cycle       последний цикл
/status      доступность и кэш
/cancel      сброс сценария
```

`/aero` всегда строит один согласованный Skew-T log-P с годографом.

## Вертикальный профиль `/profile`

```text
/profile Москва +24
/profile 59.939 30.316 run=20260714/00 +12
```

Telegram-сводка показывает T/Td в °C, RH в %, `Zg` MSL и метеорологический ветер «откуда»/м/с. Telegram CSV:

```text
p_hPa,Zg_m_MSL,T_C,Td_C,RH_pct,wind_from_deg,wind_speed_ms
```

## Аэрологическая диаграмма `/aero`

```text
/aero Москва +24
/aero 59.939 30.316 run=20260714/00 +12
```

Common `messenger/aero_service.py` отвечает за parser, lead validation, actual run, progress и result. PNG содержит Skew-T log-P, T/Td, parcel, ice saturation, CAPE/CIN, LCL, изотермы, модельные облачные/icing/CAT layers, ветер и годограф.

## Срок × уровень `/windgram`

```text
/windgram Москва
/windgram Москва to=240 step=12 param=temp
/windgram 55.75 37.62 from=12 to=120 step=6 top=700 param=rh
```

Common service формирует допустимые сроки и проверяет публикацию максимального lead. Если новый GFS cycle ещё не содержит дальний срок, используется предыдущий подходящий run.

## Облака и явления `/cloudgram`

```text
/cloudgram Москва
/cloudgram Москва to=72 step=3 mode=pro
/cloudgram 55.75 37.62 to=120 step=6 mode=simple
```

Common `messenger/cloudgram_service.py` использует существующее метеорологическое ядро. Режим `pro` показывается пользователю как `Подробно`, `simple` — `Кратко`. Cycle выбирается по максимальному требуемому lead.

Сводка содержит actual run/cycle UTC, valid range, requested point, GFS grid, max hazard и missing fields. Гроза/опасность явно называются модельной диагностикой, не наблюдаемым явлением.

## Метеограмма `/meteogram`

```text
/meteogram Москва source=gfs days=5
/meteogram Москва source=gfs days=5 format=pdf
/meteogram Москва ensemble=gefs days=10 format=docx
```

Wizard: `точка → одна модель/ансамбль → модель → период → PNG/DOCX/PDF → подтверждение`.

Доступны GFS, ECMWF IFS/AIFS, ICON Global, GEM/GDPS, GEFS, ECMWF ENS/AIFS ENS, ICON-EPS и GEPS. Разные ансамбли не смешиваются.

Для временной GFS-метеограммы используется Open-Meteo `gfs_seamless`; нативные вертикальные `/profile`, `/aero`, `/windgram`, `/cloudgram` продолжают работать с GFS 0.25° через NOMADS/GRIB2.

## Маршрутный профиль `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

Сетка 25/50/100 км. `simple` и `pro` используют одинаковые исходные данные и risk contract; отличается представление.

## Персональные точки и параметры

Telegram:

```env
TELEGRAM_PREFERENCES_DB=.cache_gfs/telegram_preferences.sqlite3
```

MAX/VK:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

`run/cycle`, process-local callback state и geocoder candidates не сохраняются.

## Скрытая команда администратора

```env
TELEGRAM_ADMIN_IDS=123456789,987654321
TELEGRAM_ADMIN_DB=.cache_gfs/admin_stats.sqlite3
```

Admin-команда не публикуется в пользовательском меню.

## Расписания

Telegram `/schedule` хранит immutable snapshot продукта в `.cache_gfs/telegram_schedules.json`. Автоматический запуск использует актуальные модельные данные и не меняет интерактивные defaults.

## Важно

Все продукты — модельные. GFS не является наблюдением или радиозондом. Метеограммы, маршрутные продукты и локальные шкалы риска не являются официальным авиационным прогнозом/разрешением на полёт; перед полётом обязательны актуальные METAR/TAF/SIGMET/GAMET, NOTAM и решение командира.
