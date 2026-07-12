# 🌦️ Профиль атмосферы GFS 0.25

Telegram-бот и веб-интерфейс для модельных вертикальных и временных продуктов GFS 0.25. Это **модельная точка GFS**, не радиозонд, не метеостанция и не наблюдение.

## Что умеет

- 📍 Точка по координатам, городу или Telegram-геолокации.
- 🇷🇺 DaData Suggestions — основной геокодер; локальный справочник и Nominatim — резерв.
- ⏱️ Сроки GFS до `+384 ч` для профильных продуктов.
- 🔄 Progress: публикация файла, загрузка GRIB2, чтение `cfgrib/eccodes`, построение PNG/CSV.
- ⚡ NOMADS GRIB Filter: скачивается subset, а не глобальный GRIB.
- 💾 Файловый кэш по циклу, сроку, точке, уровням и продукту.
- 📈 `/profile` — вертикальный профиль T/Td/RH/ветер/изотермы.
- ✈️ `/route` — разрез вдоль маршрута до 500 гПа: ETA из скорости, сетка 25/50/100 км, единый объективный риск и два существенно разных рендера.
- 🧾 `/aero` и `/skewt` — аэрологические диаграммы.
- 🟦 `/windgram` — срок × уровень: ветер, температура или влажность.
- ☁️ `/cloudgram` — облачность, осадки, явления, видимость и грозовой риск.
- 🗺️ `/map` — одна PNG, серия PNG или MP4-анимация.
- 🔐 `/admin` — статистика использования и CSV-отчёты.

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
bash deploy_telegram_bot.sh --yes
```

Проверка:

```bash
python -m unittest discover -s tests
python runtime_check.py
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

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

### Один расчёт — два представления

`simple` и `pro` используют одинаковые исходные точки, сроки, T/RH/U/V/HGT, диагностические маски, `point_risk` и карточки. Сглаживание применяется только к display-сетке PNG и не меняет CSV или риск.

### Упрощённый режим

- плотная display-сетка;
- PCHIP и усиленное Gaussian smoothing;
- непрерывный температурный градиент;
- мягкие полупрозрачные облачные массы;
- плавные зоны льда и болтанки;
- векторные облака, снежинки, турбулентность, ветер и гроза;
- округлённые карточки;
- без barbs и RH-контуров.

### Профессиональный режим

- минимальное display-сглаживание;
- слабый температурный фон;
- CLD, ICE1/ICE2+, TURB1/TURB2+;
- RH80/90;
- красные изотермы 0/-10/-20 °C;
- V20/V30/V40;
- ветровые барбы на исходных точках GFS;
- TSRA/RA и числовые карточки.

Подробности:

- [`docs/ROUTE_PROFILE.md`](docs/ROUTE_PROFILE.md)
- [`docs/ROUTE_PROFILE_RENDERING.md`](docs/ROUTE_PROFILE_RENDERING.md)
- [`docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md`](docs/ROUTE_PROFILE_VISUAL_REQUIREMENTS.md)
- [`docs/ROUTE_RISK_CONTRACT.md`](docs/ROUTE_RISK_CONTRACT.md)

## Admin

```env
TELEGRAM_ADMIN_IDS=123456789,987654321
TELEGRAM_ADMIN_DB=.cache_gfs/admin_stats.sqlite3
```

## Важно

GFS — модельная сетка, не радиозонд и не наблюдение. Сглаженная картинка не повышает физическое разрешение GFS. `/route` не определяет юридическую или фактическую возможность полёта. Перед полётом обязательны актуальные METAR/TAF/SIGMET/GAMET, NOTAM, данные диспетчерских и решение командира.
