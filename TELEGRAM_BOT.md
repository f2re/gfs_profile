# 🤖 Telegram-бот GFS

Telegram остаётся самым полным UI проекта, но метеорологическая логика постепенно вынесена в messenger-neutral services. `/profile`, `/aero`, `/windgram` и `/cloudgram` уже используют те же common services, что MAX/VK; Telegram отвечает за wizard, native controls, status и отправку media.

## Команды

```text
/start       главное меню
/help        краткая инструкция
/cancel      сброс текущего выбора
/cycle       последний цикл GFS
/status      доступность и кэш
/profile     вертикальный профиль
/route       профиль по маршруту
/aero        Skew-T log-P + годограф
/windgram    срок × уровень
/cloudgram   облака, осадки, видимость, гроза
/meteogram   модель/ансамбль, PNG/DOCX/PDF
/map         карта, серия, анимация
/schedule    автоматическая отправка
/settings    точки, параметры и recipes
/admin       скрытая административная команда
```

`/skewt` удалена: `/aero` всегда означает один согласованный Skew-T log-P.

## Главное меню и персональное состояние

`/start` показывает стабильные inline-продукты, основную точку и до двух quick recipes. Геолокационная reply-кнопка показывается только на стадии выбора точки.

Приоритет:

```text
явные параметры команды
→ текущий сохранённый выбор
→ последний успешный расчёт
→ default
```

Не сохраняются `run/cycle`, GRIB file, geocoder candidates, message/progress ids и callback state. Repeat заново выбирает актуальный опубликованный cycle.

```env
TELEGRAM_PREFERENCES_DB=.cache_gfs/telegram_preferences.sqlite3
```

## `/profile`

```text
/profile Москва +24
/profile 59.939 30.316 run=20260714/00 +12
```

Используется общий `messenger/profile_service.py`. Сводка показывает T/Td в °C, RH %, Zg MSL и ветер «откуда» в м/с. Telegram CSV:

```text
p_hPa,Zg_m_MSL,T_C,Td_C,RH_pct,wind_from_deg,wind_speed_ms
```

## `/aero`

```text
/aero Москва +24
```

Общий `messenger/aero_service.py` выбирает actual run, формирует progress/result. PNG содержит Skew-T, T/Td, parcel, ice saturation, CAPE/CIN, LCL, изотермы, облачные/icing/CAT model proxies, ветер и годограф.

## `/windgram`

Default:

```text
ветер
+0…+120 ч
шаг 6 ч
до 500 гПа
```

```text
/windgram Москва to=240 step=12 param=temp
```

Общий `messenger/windgram_service.py` проверяет публикацию максимального требуемого lead. Доступны wind/temp/RH, горизонты 120/240/384 и шаг 3/6/12.

## `/cloudgram`

Default:

```text
Подробно
+0…+72 ч
шаг 3 ч
```

```text
/cloudgram Москва to=72 step=3 mode=pro
/cloudgram Москва to=120 step=6 mode=simple
```

Telegram вызывает общий `messenger/cloudgram_service.py`. Режимы в UI: `Подробно` и `Кратко`. Common result показывает actual run/cycle UTC, valid range, requested point, GFS grid, max model hazard, missing fields и PNG.

Гроза/опасность всегда подписываются как модельная диагностика, не наблюдавшееся явление.

Подробно: `docs/MESSENGER_CLOUDGRAM_SERVICE.md`.

## `/map`

Первый запуск:

```text
Анимация
+0…+48 ч
шаг 3 ч
17 кадров
радиус 100 км
```

`/map Москва +24` — одна карта. Для длинных периодов шаг автоматически приводится к лимиту кадров. Настройки animation/single/series сохраняются независимо.

## `/meteogram`

Wizard:

```text
точка
→ одна модель / ансамбль
→ модель
→ период
→ PNG / DOCX / PDF
→ подтверждение
```

Доступны GFS, ECMWF IFS/AIFS, ICON, GEM и их поддерживаемые ensemble systems. Разные ансамбли не смешиваются. Временной GFS surface ряд использует Open-Meteo GFS Seamless; вертикальные `/profile`, `/aero`, `/windgram`, `/cloudgram` продолжают работать с GFS 0.25 через NOMADS/GRIB2.

## `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

Сетка 25/50/100 км. Route endpoints не заменяют active point пользователя.

## Progress

Долгая операция редактирует одно status message. Пользователь видит точку, период/lead и понятный этап без внутренних названий библиотек.

Common products используют одинаковый progress contract; Telegram только отображает его.

## Recipes

После успешного расчёта сохраняется точный recipe с point+params. Result actions адресуют конкретный `recipe_id`, поэтому несколько вариантов одного продукта не конфликтуют.

`/settings → Сохранённые сценарии` позволяет повторить, изменить, pin/unpin, поставить в расписание или удалить рецепт.

## Расписания

Telegram scheduler хранит immutable product snapshot в:

```text
.cache_gfs/telegram_schedules.json
```

Scheduled run не меняет active point/preferences и всегда использует актуальные model data.

## Установка/deploy

Штатный systemd entrypoint уже multi-messenger:

```text
messenger_launcher.py
```

```bash
git checkout telegram-bot
git pull --ff-only
python -m unittest discover -s tests
python runtime_check.py
sudo bash deploy_telegram_bot.sh --yes
```

MAX/VK подключаются отдельно после получения platform credentials:

```bash
sudo bash setup_messenger_bots.sh --max
sudo bash setup_messenger_bots.sh --vk
```

Пошагово: `docs/MESSENGER_REGISTRATION.md`.

## Важно

Все данные — модельные. GFS не радиозонд и не наблюдение. Aviation hazards/icing/CAT/cloud/thunder layers являются модельной диагностикой и не заменяют METAR/TAF/SIGMET/GAMET/NOTAM и оперативное решение.
