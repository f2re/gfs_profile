# 🤖 Telegram-бот GFS

Telegram сохраняет native wizard/UI, но все семь основных продуктов уже используют messenger-neutral services — те же расчёты и результаты, что MAX/VK.

Итоговый паритет: [`docs/MESSENGER_PARITY.md`](docs/MESSENGER_PARITY.md).

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
/cloudgram   облака, осадки, видимость, риски
/meteogram   модель/ансамбль, PNG/DOCX/PDF
/map         карта, серия, анимация
/schedule    автоматическая отправка
/settings    точки, параметры и recipes
/admin       скрытая административная команда
```

`/skewt` удалена: `/aero` всегда означает один согласованный Skew-T log-P.

## Common product layer

```text
/profile   → messenger/profile_service.py
/aero      → messenger/aero_service.py
/windgram  → messenger/windgram_service.py
/cloudgram → messenger/cloudgram_service.py
/map       → messenger/map_service.py
/meteogram → messenger/meteogram_service.py
/route     → messenger/route_service.py
```

Telegram handlers отвечают за native controls/status/media. GFS calculations, actual run selection и result data не реализуются второй раз.

## Персональное состояние

`/start` показывает стабильное меню, основную точку и до двух quick recipes.

Приоритет:

```text
явные параметры команды
→ текущий сохранённый выбор
→ последний успешный расчёт
→ default
```

Не сохраняются:

```text
run/cycle
GRIB file
geocoder candidates
message/progress ids
callback state
```

```env
TELEGRAM_PREFERENCES_DB=.cache_gfs/telegram_preferences.sqlite3
```

Route endpoints не заменяют active point.

## Defaults

```text
/profile     +24 ч
/aero        +24 ч, Skew-T
/windgram    wind, +0…+120, step 6, top 500 hPa
/cloudgram   Подробно, +0…+72, step 3
/map         MP4 +0…+48, step 3, radius 100, places
/meteogram   GFS, 5 суток, PNG
/route       +24, 300 км/ч, simple, grid 50 км
```

## `/profile`

```text
/profile Москва +24
/profile 59.939 30.316 run=20260714/00 +12
```

CSV:

```text
p_hPa,Zg_m_MSL,T_C,Td_C,RH_pct,wind_from_deg,wind_speed_ms
```

T/Td — °C, Zg — MSL, ветер — направление «откуда» и м/с.

## `/aero`

Один Skew-T log-P + годограф. Включает CAPE/CIN, LCL, изотермы, облачные/icing/CAT model proxies. Фактический GFS run и valid UTC приходят из common service.

## `/windgram`

```text
/windgram Москва to=240 step=12 param=temp
```

Wind/temp/RH, горизонты до +384. Cycle проверяется по максимальному требуемому lead.

## `/cloudgram`

```text
/cloudgram Москва to=72 step=3 mode=pro
/cloudgram Москва to=120 step=6 mode=simple
```

UI: `Подробно/Кратко`. Hazard/thunder — модельная диагностика, не наблюдавшееся явление.

## `/map`

```text
/map Москва
/map Москва +24
/map Москва from=0 to=96 step=6 mode=gif
```

Default animation: +0…+48 ч, step 3, 17 кадров, radius 100 км. Для длинных диапазонов step автоматически приводится к лимиту кадров. Telegram отправляет silent H.264/MP4 с fallback при необходимости.

## `/meteogram`

Wizard:

```text
точка → deterministic/ensemble → модель → период → PNG/DOCX/PDF → подтверждение
```

Доступны GFS, ECMWF IFS/AIFS, ICON, GEM, GEFS, ECMWF ENS/AIFS ENS, ICON-EPS, GEPS. Разные ансамбли не смешиваются. Если upstream не сообщает model cycle, бот не выдумывает его.

## `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

Run выбирается по max ETA lead. Result — PNG+CSV. `simple/pro` используют одинаковые данные/risk contract.

## Recipes

Successful result создаёт recipe `point + params`; `run/cycle` исключены. Действия адресуют конкретный recipe id, поэтому несколько вариантов одной карты/продукта не конфликтуют.

`/settings` позволяет выбирать active point, повторять/закреплять/удалять recipes и очищать персональные данные.

## Расписания

Telegram сохраняет native scheduler и storage:

```env
TELEGRAM_SCHEDULE_FILE=.cache_gfs/telegram_schedules.json
```

Доступны **все семь продуктов**, включая `/route`.

Route добавлен adapter-ом `telegram_schedule_route_compat.py`: native route wizard формирует immutable schedule spec, а automatic execution вызывает common route runner. Метеорологическая логика не копируется.

Scheduled snapshot не хранит `run/cycle`, не обновляет active point/preferences и при каждом запуске использует актуальные model data.

Подробно: [`docs/MESSENGER_SCHEDULES.md`](docs/MESSENGER_SCHEDULES.md).

## Platform isolation

```env
TELEGRAM_ENABLED=auto
```

Если Telegram polling/token сломан, FastAPI runtime остаётся доступен MAX/VK/web. `/health` показывает Telegram как `degraded`; соседние платформы продолжают работу.

Аварийно можно отключить только Telegram:

```env
TELEGRAM_ENABLED=0
```

## Production runtime

Systemd запускает:

```text
messenger_launcher.py
```

Общие process-wide limits:

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_SCHEDULED=1
```

## Deploy

```bash
git checkout telegram-bot
git pull --ff-only
python -m unittest discover -s tests
python runtime_check.py
sudo bash deploy_telegram_bot.sh --yes
```

MAX/VK регистрация: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md).

## Важно

Все данные модельные. GFS — не наблюдение и не радиозонд. Icing/CAT/cloud/thunder/hazard layers — модельная диагностика и не заменяют официальные METAR/TAF/SIGMET/GAMET/NOTAM и эксплуатационное решение.