# Общий `/cloudgram` для Telegram, MAX и VK

## Назначение

`messenger/cloudgram_service.py` — единый transport-neutral use-case облачности, осадков, видимости, ВНГО и конвективной диагностики GFS. Telegram/MAX/VK не дублируют выбор run, парсинг параметров, расчёт или PNG.

Расчётное ядро остаётся:

```text
cloudgram_product.py
cloudgram_render.py
weather_diagnostics.py
```

## Defaults

```text
from=0
to=72
step=3
mode=pro  # UI: Подробно
```

Интерактивные варианты MAX/VK и Telegram:

```text
Режим: Подробно / Кратко
Период: +24 / +48 / +72 / +120 ч
Шаг: 3 / 6 ч
```

Прямые команды:

```text
/cloudgram Москва to=72 step=3 mode=pro
/cloudgram 55.75 37.62 from=12 to=120 step=6 mode=simple
```

## Выбор GFS run

Сначала формируется фактический список канонических lead через `cloudgram_leads()`. Автовыбор проверяет публикацию максимального требуемого lead:

```text
max(leads) → latest_available_run_for_lead(max_lead)
```

Если новый цикл ещё не содержит дальний срок, выбирается предыдущий опубликованный цикл. Explicit `run=YYYYMMDD/HH` сохраняет прямую семантику команды.

`run/cycle` никогда не записываются в saved recipe.

## Common result

`CommonProductResult` содержит:

```text
product=cloudgram
summary
PNG attachment
repeat_command
metadata:
  model=GFS 0.25
  data_kind=model
  source=NOMADS GRIB Filter
  run_date/run_cycle
  lead_from/lead_to/step/mode
  requested_lat/lon
  grid_lat/lon
  max_hazard
  missing_fields
```

Сводка показывает actual run/cycle UTC, valid range UTC, requested point и GFS grid point.

## Метеорологический contract

Используются существующие GFS поля/диагностика проекта, включая:

- low/mid/high/total cloud;
- convective cloud;
- APCP/PRATE и convective precip;
- rain/snow/freezing/ice-pellet flags;
- CAPE/CIN, приоритет 180–0 hPa AGL;
- visibility;
- cloud ceiling как нативный AGL product;
- явления и hazard score.

`20000 м` для GFS cloud ceiling трактуется как «потолка нет», а не как физический ВНГО.

Hazard/thunder — **модельная диагностика**, не сообщение о наблюдавшемся явлении. В пользовательском тексте сохраняется маркировка:

```text
GFS grid • модель, не наблюдение и не радиозонд
```

## MAX/VK flow

```text
/cloudgram
→ точка / город / координаты / native geo
→ неоднозначность при необходимости
→ карточка параметров
→ Построить
→ одно редактируемое status message
→ summary + PNG
→ recipe card
```

Direct command с однозначной точкой сразу запускает расчёт. При ambiguity все параметры запроса и explicit run сохраняются до callback выбора пункта.

## Saved recipe

```text
product=cloudgram
point={lat, lon, label, source}
params={from, to, step, mode}
```

Pinned/latest recipe открывается командой `/cloudgram` без параметров. Repeat передаёт `run=None` и заново выбирает опубликованный cycle по максимальному lead.

## Проверки

Обязательные tests:

- parser defaults и aliases `Подробно/Кратко`;
- max-lead cycle selection;
- model/result metadata;
- Telegram вызывает common service;
- MAX/VK direct command;
- city/ambiguity/location;
- parameter callbacks;
- recipe/pin/repeat без run;
- cleanup временного PNG;
- старые hazard/product/render tests остаются зелёными.
