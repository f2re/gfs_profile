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
- 🧾 `/aero` и `/skewt` — аэрологические диаграммы.
- 🟦 `/windgram` — срок × уровень: ветер, температура или влажность.
- ☁️ `/cloudgram` — облачность, осадки, видимость и грозовой риск.
- 🗺️ `/map` — одна карта, серия PNG или MP4-анимация.
- 🔐 `/admin` — статистика и CSV-отчёты.

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

`/start` показывает короткое описание продуктов и inline-кнопки выбора. Кнопка отправки геолокации не находится на экране постоянно.

Reply-клавиатура появляется только на стадии выбора точки:

```text
📍 Моя геолокация
последние точки
✖ Отмена
```

После выбора точки она удаляется, а срок и параметры настраиваются inline-кнопками. `/cancel` очищает сценарий и возвращает главное меню.

Общие дисклеймеры не повторяются в `/start`, `/help`, progress и команде повтора. Научная маркировка остаётся в итоговой сводке и footer PNG.

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
/aero       аэродиаграмма
/skewt      быстрая Skew-T
/windgram   срок × уровень
/cloudgram  облака, осадки и грозы
/map        карта, серия или анимация
/cycle      последний цикл
/status     доступность и кэш
/admin      администрирование
/cancel     сброс сценария
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

`simple` и `pro` используют одинаковые исходные данные, `point_risk` и карточки. Сглаживание применяется только к display-сетке PNG.

### Упрощённый режим

- PCHIP только для непрерывных полей;
- линейная интерполяция категориальных масок;
- умеренное сглаживание без искусственного слияния зон;
- мягкие облака и существенные зоны льда/болтанки;
- ограниченное число подписей и пиктограмм;
- адаптивные карточки примерно по 100–120 км.

### Профессиональный режим

- минимальное display-сглаживание;
- CLD, ICE1/ICE2+, TURB1/TURB2+;
- RH80/90 и красные изотермы;
- V20/V30/V40 и ветровые барбы;
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

Сглаженная картинка не повышает физическое разрешение GFS. Маршрутный продукт не является разрешением на полёт; перед полётом обязательны актуальные METAR/TAF/SIGMET/GAMET, NOTAM и решение командира.
