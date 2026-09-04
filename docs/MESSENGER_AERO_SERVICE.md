# Общий `/aero` для Telegram, MAX и VK

## Назначение

`/aero` — второй после `/profile` продукт, вынесенный в messenger-neutral service. Telegram, MAX и VK используют один и тот же расчётный путь:

```text
команда / callback / location
→ normalized request
→ geocoder
→ messenger/aero_service.py
→ фактический опубликованный GFS run для требуемого lead
→ aero_product.py
→ diagnostic_profile / Skew-T / годограф
→ CommonProductResult
→ platform renderer
```

Метеорологические расчёты, выбор полей GRIB, диагностические индексы и отрисовка не копируются в adapters.

## Общий service

`messenger/aero_service.py` отвечает за:

- разбор публичного `/aero`;
- совместимость со старым `type=...` без возврата выбора типа диаграммы;
- валидацию lead до `+384 ч`;
- выбор актуального опубликованного GFS run, если `run=` явно не задан;
- вызов существующего `aero_product.build_aero_product`;
- преобразование progress в `ProgressEvent`;
- формирование `CommonProductResult` и PNG attachment;
- единый repeat command.

Пользовательский продукт всегда:

```text
Skew-T log-P + годограф
```

Stüve/Emagram не возвращаются в UI.

## Метеорологический контракт результата

Common result содержит:

- `GFS 0.25` и явную маркировку как модель;
- фактический run/cycle UTC;
- lead и valid UTC;
- requested point;
- GFS grid point;
- Skew-T log-P и годограф;
- `Zg MSL`;
- icing/CAT как **модельные прокси**;
- PNG;
- повторяемую команду с фактическим run.

Диаграмма не называется радиозондом или наблюдением.

Физические формулы, CAPE/CIN, LCL/LFC/EL, облачные слои, SLWC icing proxy и CAT/Ri описаны отдельно в `docs/AERO_DIAGRAM.md` и `docs/METEOROLOGICAL_METHODS.md`.

## UX MAX/VK

Поддерживаются те же базовые сценарии, что для общего профиля:

```text
/aero
→ город / координаты / native location
→ при неоднозначности выбор точки
→ выбор срока
→ расчёт
```

Прямой запрос:

```text
/aero Москва +24
```

сразу запускает расчёт без лишнего шага срока.

Быстрые сроки:

```text
+0 +3 +6 +12 +24 +48
```

Все канонические сроки до `+384` доступны через пагинацию.

Долгий расчёт редактирует одно status message:

1. проверка опубликованного цикла;
2. узел GFS;
3. загрузка/кэш;
4. расчёт профиля и диагностики;
5. Skew-T/годограф и PNG.

## Telegram

`telegram_aero.py` остаётся Telegram adapter и compatibility surface для существующих handlers/tests, но больше не владеет:

- выбором GFS run;
- вызовом метеорологического ядра напрямую;
- построением собственного результата.

Он вызывает `build_aero_product_result`, отображает общий progress/result и отправляет common attachments.

Таким образом одинаковые координаты, run и lead используют один расчётный use-case во всех мессенджерах.

## Saved recipes

После успешного `/aero` MAX/VK сохраняют сценарий через общий `UserRecipeStore`:

```text
product=aero
point={lat, lon, label}
params={lead, diagram_type=skewt}
```

`run/cycle` в recipe не сохраняются. Поэтому быстрый повтор и закреплённый сценарий каждый раз выбирают свежий опубликованный GFS cycle, если пользователь не ввёл `run=` явно в новом запросе.

Callbacks используют устойчивый `recipe_id` и переживают restart процесса.

Telegram использует тот же логический recipe contract через свой персональный UX слой.

## Границы текущего паритета

После этого vertical slice common messenger runtime поддерживает:

```text
/profile
/aero
```

Следующий продукт для переноса — `/windgram`, затем `/cloudgram`, `/meteogram`, `/map` и остальные flow.

## Проверки

Минимальный contract suite:

- parser legacy `type=` → всё равно Skew-T;
- explicit run не заменяется новым циклом;
- default run выбирается для требуемого lead;
- CommonProductResult содержит фактический run/valid/requested/grid;
- Telegram использует общий builder;
- MAX/VK: direct city + lead;
- ambiguous city;
- native location;
- lead pagination;
- saved recipe/pin/repeat;
- отказ run selection показывается пользователю как ошибка, а не fallback на другой продукт.

Перед merge дополнительно проходят общий `runtime_check.py`, полный unittest suite и live GFS smoke проекта.
