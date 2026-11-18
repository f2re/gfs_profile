# Общий `/windgram` для Telegram, MAX и VK

## Назначение

`/windgram` — третий messenger-neutral vertical slice после `/profile` и `/aero`. Все три платформы используют один `messenger/windgram_service.py`; transport-слои не выбирают GFS run и не выполняют метеорологические расчёты самостоятельно.

```text
Telegram / MAX / VK
        ↓
Normalized request
        ↓
messenger/windgram_service.py
        ↓
windgram_product.py + windgram_plot.py
        ↓
CommonProductResult
        ↓
platform renderer
```

GFS всегда обозначается как модель, не наблюдение и не радиозонд.

## Параметры

Первый интерактивный запуск использует:

```text
параметр: ветер
период: +0…+120 ч
шаг: 6 ч
верхний уровень: 500 гПа
```

Доступны:

```text
param: wind | temp | rh
период: до +120 | +240 | +384 ч
шаг: 3 | 6 | 12 ч
```

Прямая команда сохраняет расширенный синтаксис:

```text
/windgram Москва
/windgram Москва to=240 step=12 param=temp
/windgram 55.75 37.62 from=12 to=120 step=6 top=700 param=rh
/windgram Москва run=20260905/00 to=120
```

`run=` применяется только к конкретному прямому расчёту. В saved recipe цикл не записывается.

## Выбор GFS run

Service сначала формирует реальные канонические сроки через `windgram_leads()`, затем проверяет публикацию по максимальному требуемому lead:

```text
leads → max(leads) → latest_available_run_for_lead(max_lead)
```

Если новый цикл ещё не содержит дальний срок, выбирается предыдущий опубликованный цикл. Поэтому нельзя выбирать запуск только по `f000`.

Repeat saved recipe передаёт `run=None` и заново выполняет это правило.

## Результат

`CommonProductResult` содержит:

- фактический GFS run/cycle UTC;
- период и шаг;
- valid UTC диапазон;
- requested point;
- GFS grid point;
- число уровней и верхний уровень;
- выбранный параметр;
- максимальную скорость ветра, если она доступна;
- PNG;
- platform-neutral repeat command.

Сводка заканчивается явной маркировкой:

```text
GFS grid • модель, не наблюдение и не радиозонд
```

## Progress

Платформы редактируют одно status message. Общий progress contract покрывает:

```text
1/6 проверка опубликованного цикла
2/6 определение узла GFS
3/6 загрузка сроков
4/6 чтение профилей и формирование матрицы
5/6 формирование PNG
6/6 отправка результата
```

Внутренние названия библиотек пользователю не показываются.

## MAX/VK flow

```text
/windgram
→ город / координаты / native location
→ карточка параметров
→ Построить
```

Карточка позволяет выбрать `ветер/температура/влажность`, горизонт `120/240/384 ч` и шаг `3/6/12 ч`.

Неоднозначный город сохраняет все выбранные параметры и, для прямой команды, явный `run=` до выбора конкретного пункта.

## Telegram flow

Существующий Telegram wizard сохраняется. Telegram adapter использует тот же common builder и отвечает только за status/PNG/Telegram-specific UI.

Это означает, что исправление расчёта или выбора GFS run делается один раз в common service и сразу применяется ко всем трём платформам.

## Saved recipes

После успешного интерактивного результата сохраняются:

```text
from
to
step
top
param
point
```

Не сохраняются:

```text
run/cycle
GRIB filename
progress/message ids
callback state
```

Pin/repeat используют устойчивый `recipe_id`. MAX и VK разделены по `platform + user_id`.

## Ресурсы

Все расчёты проходят через общий process-wide `RuntimeResources.gfs_semaphore`. Поэтому `/windgram` не получает отдельную квоту для MAX/VK поверх Telegram.

## Проверка

Минимальный contract:

```text
Telegram direct + wizard
MAX direct + wizard + geo + ambiguous city
VK direct + wizard + geo + ambiguous city
wind/temp/rh
120/240/384 ч
3/6/12 ч
recipe + pin + repeat
actual max-lead run selection
PNG + model disclaimer
```

Live GFS smoke запускается на общем meteorological core один раз; transport parity проверяется быстрыми fake-gateway tests.
