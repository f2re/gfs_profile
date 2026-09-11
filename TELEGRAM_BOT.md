# 🤖 Telegram-бот GFS + WeatherNext 3

## WeatherNext временно отключён

По умолчанию `WEATHERNEXT3_ENABLED=0` (отсутствующее или неизвестное значение также
означает отключение). Наличие старых Google credentials не включает источник.
WeatherNext не показывается в меню, справке, обычных и ансамблевых моделях Telegram,
MAX и VK. Старые команды, кнопки и прямые запросы не запускают расчёт. API WN3
не регистрируется; нет импорта его провайдеров, обработки кэша или задач WN3 при
старте службы. Обычные GFS-продукты и другие источники остаются доступны.

Сохранённые сценарии и расписания WN3 (включая `meteogram` с `source/source_id=wn3`)
скрыты и не исполняются. Записи, `.env`, `.venv` и кэш не удаляются. После обычного
обновления выполните `bash deploy_telegram_bot.sh`; одного `git pull` для копии в
`/opt/gfs_profile` недостаточно. Из старых сообщений кнопки нельзя убрать массово,
но нажатия блокируются; `/start` открывает чистое меню.

`requirements.txt` больше не устанавливает Google BigQuery, Zarr и obstore.
Ранее установленные пакеты могут оставаться на диске, но не импортируются этим
источником. Повторное включение — только администратором после получения доступа:
установить `requirements-weathernext3.txt`, настроить Google, явно задать
`WEATHERNEXT3_ENABLED=1` и перезапустить службу. Для текущей работы бота этого не нужно.


Telegram сохраняет native wizard/UI, но основные продукты используют messenger-neutral services — те же расчёты и результаты, что MAX/VK. WeatherNext 3 добавлен отдельным разделом поверх общего provider/service слоя.

Итоговый паритет: [`docs/MESSENGER_PARITY.md`](docs/MESSENGER_PARITY.md). WeatherNext 3: [`docs/WEATHERNEXT3.md`](docs/WEATHERNEXT3.md).

## Команды

```text
/start       главное меню
/help        краткая инструкция
/cancel      сброс текущего выбора
/cycle       последний цикл GFS
/status      доступность и кэш
/profile     вертикальный профиль GFS
/route       профиль по маршруту GFS
/aero        Skew-T log-P + годограф GFS
/windgram    срок × уровень GFS
/cloudgram   облака, осадки, видимость, риски GFS
/meteogram   модель/ансамбль, PNG/DOCX/PDF
/map         карта, серия, анимация GFS
/wn3         WeatherNext 3: point/meteogram/cloud/precip maps
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
/wn3       → messenger/weathernext3_service.py
```

Telegram handlers отвечают за native controls/status/media. GFS calculations, WeatherNext 3 BigQuery queries, actual run selection и result data не реализуются второй раз.

## WeatherNext 3 `/wn3`

Главное меню содержит кнопку `🛰 WeatherNext 3`. Если сохранена active point, раздел открывается сразу для неё; иначе бот запрашивает город, координаты или native Telegram location.

При входе раздел открывает карточку параметров point на +24 ч, без платного запроса до нажатия «Построить». Для карт default:

```text
WeatherNext 3 · combo
+1…+48 ч
step 3 ч
radius 150 км
animation MP4
```

Кнопки раздела:

```text
🌡 Прогноз +N ч
📊 Метеограмма
☁️ Облачность
☁️ Слои
🌧 WN3 native precip
🛰 IMERG precip
🧪 Experimental precip
🌦 Облака + осадки
период / step / radius / animation
```

Прямые команды:

```text
/wn3 Москва
/wn3 Москва +24
/wn3 Москва kind=clouds to=48 step=3 radius=150
/wn3 Москва kind=precip_imerg to=48 step=3
/wn3 Москва kind=combo to=72 step=6 radius=250
```

Во время BigQuery/render операции редактируется одно status message. Итог показывает фактический WN3 `Run ...Z`, valid UTC, requested point/grid и маркировку «модельный прогноз».

T/Td в point/meteogram используют station head 0.05° при наличии, остальные surface fields — 0.1°. Карты показывают выбранную статистику; число реально доступных членов BigQuery не сообщает. Метеограмма дополнительно использует p10/p25/p50/p75/p90.

Вертикальная WN3 подключена через GCS: `/wn3 Москва kind=profile +24`, `kind=aero`, `kind=windgram`. Обычные `/profile` и `/aero` остаются GFS.

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
/wn3         point +24; карты: +1…+48, step 3, radius 150
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

Доступны GFS, ECMWF IFS/AIFS, ICON, GEM, GEFS, ECMWF ENS/AIFS ENS, ICON-EPS, GEPS и WeatherNext 3 statistics. Разные ансамбли не смешиваются. Если upstream не сообщает model cycle, бот не выдумывает его. В `/wn3` WN3 init показывается явно.

## `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

Run выбирается по max ETA lead. Result — PNG+CSV. `simple/pro` используют одинаковые данные/risk contract.

## Recipes

Successful result создаёт recipe `point + params`; `run/cycle` исключены. Действия адресуют конкретный recipe id, поэтому несколько вариантов одной карты/продукта не конфликтуют.

`/settings` позволяет выбирать active point, повторять/закреплять/удалять recipes и очищать персональные данные.

WN3 сохраняет owner-bound карточки, сценарии и расписания в общем SQLite. В native настройках/расписаниях есть переходы в WN3.

## Расписания

Telegram сохраняет native scheduler и storage:

```env
TELEGRAM_SCHEDULE_FILE=.cache_gfs/telegram_schedules.json
```

Доступны семь исходных продуктов и WN3; автоматическая WN3-отправка требует общего production runtime.

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
MAX_CONCURRENT_WEATHERNEXT3=2
MAX_CONCURRENT_SCHEDULED=1
```

## WeatherNext 3 config

```env
WEATHERNEXT3_BIGQUERY_PROJECT=<project-with-linked-dataset>
WEATHERNEXT3_BIGQUERY_DATASET=<linked-dataset>
WEATHERNEXT3_BIGQUERY_BILLING_PROJECT=
WEATHERNEXT3_BQ_MAX_BYTES_BILLED=1000000000
WEATHERNEXT3_CACHE_TTL=1800
GOOGLE_APPLICATION_CREDENTIALS=/path/outside/repo/credentials.json
```

Подробно: [`docs/WEATHERNEXT3.md`](docs/WEATHERNEXT3.md).

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

Все данные модельные. GFS — не наблюдение и не радиозонд. WeatherNext 3 — экспериментальная AI-модель Google, не наблюдение/радар/спутниковый снимок и не официальный warning source. Icing/CAT/cloud/thunder/hazard layers — модельная диагностика и не заменяют официальные METAR/TAF/SIGMET/GAMET/NOTAM и эксплуатационное решение.


## WN3 RC: расширение 8 сентября 2026

Общий сценарий Telegram/MAX/VK: owner-bound кнопки после перезапуска, пагинация +1…+360, статус/отмена, защита от двойного запуска, сценарии/повтор/расписания. BigQuery: точка, метеограмма PNG/DOCX/PDF, облачность по времени, три варианта осадков, карты/MP4/GIF/CSV, T2, p90−p10, ветер 100 м и радиация. GCS: profile/aero/windgram на 13 уровнях, средний профиль или member=0..63.

Точные команды, подключение и ограничения: [WEATHERNEXT3.md](docs/WEATHERNEXT3.md). Google live-доступ и production-доставка не проверены этим RC. Маршрут WN3 и вероятности событий не заявляются реализованными.

Для GCS-профиля, аэродиаграммы и ветровой матрицы WN3 требуется Python 3.11+. Python 3.10 поддерживает GFS и поверхностную WN3 через BigQuery; неподдерживаемые зависимости Zarr на нём не устанавливаются.


## WN3: два вида метеограммы (RC2)

В обычном выборе моделей доступен **WeatherNext 3 · средний прогноз**,
в ансамблевом — среднее и квантили. В `/wn3` это отдельные кнопки
«Метеограмма» (`kind=meteogram`) и «Ансамбль / разброс» (`kind=ensemble`).
Это два представления одного ансамбля, не отдельная детерминированная модель.
Старый `source=weathernext3` сохранён; среднее доступно как `source=weathernext3_mean`.
Общий сценарий работает в Telegram, MAX и VK, включая PNG/DOCX/PDF/CSV.
Подробности и ограничения: [WeatherNext 3](docs/WEATHERNEXT3.md).
