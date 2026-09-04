# Архитектура Telegram + MAX + VK

Дата фиксации: 2026-09-04.

## 1. Решение

Проект переходит от Telegram-centric orchestration к общей messenger architecture.

Главный принцип:

```text
одно метеорологическое ядро
+ один use-case/service слой
+ несколько transport adapters
```

Telegram, MAX и VK должны отличаться только способом приёма событий и способом отображения/отправки результата.

## 2. Текущее состояние

Расчёты уже в значительной степени отделены:

- `gfs_core.py`;
- `aero_product.py` и meteorology/render modules;
- `windgram_product.py`/plot;
- `cloudgram_product.py`/render;
- meteogram core/data/fetch/diagnostics/plot/report;
- composite map modules;
- geocoder;
- общие formatters.

Проблема находится в orchestration layer: parse/geocode/state/progress/send сильно связаны с `telegram_*`.

Следовательно, MAX/VK нельзя строить копированием Telegram modules. Сначала выделяется общий messenger use-case.

## 3. Целевая схема

```text
              ┌──────────────────┐
Telegram ────▶│ Telegram adapter │
              └────────┬─────────┘
                       │
MAX webhook ─▶ MAX adapter ──────┤
                       │          ▼
VK callback ─▶ VK adapter ──▶ NormalizedEvent
                                  │
                                  ▼
                           MessengerRouter
                                  │
                                  ▼
                           ForecastService
                                  │
          ┌───────────────────────┼───────────────────────┐
          ▼                       ▼                       ▼
       geocoder              run selector             products
                                  │                       │
                                  └──────────┬────────────┘
                                             ▼
                                      CommonResult
                                             │
                          ┌──────────────────┼──────────────────┐
                          ▼                  ▼                  ▼
                    Telegram renderer   MAX renderer       VK renderer
```

## 4. Границы ответственности

### Platform adapter

Отвечает только за:

- проверку/auth входящего события;
- extraction ids/text/location/callback;
- mapping platform event → `NormalizedEvent`;
- dedupe id;
- передачу события в router.

Не выполняет geocoding/GFS calculation.

### MessengerRouter

Отвечает за:

- command/action routing;
- восстановление session state;
- переходы wizard;
- определение common use-case;
- вызов service;
- выбор common view/action.

Не знает форматы Telegram/MAX/VK keyboard.

### ForecastService

Отвечает за:

- geocoder;
- выбор фактического опубликованного run;
- вызов продукта;
- progress events;
- common result;
- cleanup временных файлов;
- статистику результата.

Не знает message id конкретной платформы.

### Common formatter/view-model

Отвечает за смысл выдачи:

- заголовки;
- модель/run/lead/valid;
- requested/grid point;
- единицы;
- диагностические значения;
- предупреждение, что это модель.

### Platform renderer

Отвечает за:

- native keyboard;
- parse mode/escaping;
- send/edit text;
- upload/send attachments;
- callback answer;
- ограничения длины сообщений/подписей;
- fallback при platform limitation.

## 5. Common models

### NormalizedEvent

Минимально:

```text
platform
raw_event_id
event_type
user_id
chat_id
message_id
text
command
callback_payload
location(lat, lon)
timestamp
```

Event types:

```text
START
TEXT
COMMAND
LOCATION
CALLBACK
CANCEL
```

Platform lifecycle events могут существовать отдельно, но не должны попадать в forecast service без необходимости.

### Common button

```text
text
action: callback | request_location | text | link
payload
url
```

### StatusHandle

Common service не хранит native message object. Renderer возвращает лёгкий handle, пригодный для `edit_text`.

### ProductResult

Минимальная структура:

```text
product
summary
attachments[]
metadata
repeat_action
followup_actions[]
```

Attachment:

```text
kind: image | file | animation
path
filename
caption
mime_type
```

`metadata` содержит actual run/lead/grid/source и используется для contract tests/analytics.

## 6. Состояние

Разделить:

### Ephemeral session state

Wizard/current step/candidates/status handles.

Ключ:

```text
(platform, user_id, chat_id)
```

На первом этапе допустим in-memory session state при условии stateless/versioned callbacks и возможности безопасно начать flow заново после restart.

### Persistent preferences

Основная точка, последние точки, параметры продукта, быстрые действия.

Целевая общая SQLite schema:

```text
platform
user_id
...
```

Не создавать независимые бизнес-схемы под каждую платформу.

## 7. Callback policy

Callback payload:

- versioned;
- короткий;
- не содержит секретов;
- проверяется по whitelist action/product;
- позволяет восстановить действие без ссылки на Python object;
- не доверять входным координатам/lead без validation.

Предпочтительно хранить компактные устойчивые ссылки на point/preferences либо достаточный набор параметров. Ограничения payload конкретной платформы учитываются renderer/codec layer.

## 8. Webhook execution model

MAX/VK webhook handler выполняет только:

```text
validate
→ dedupe
→ normalize
→ schedule async task
→ immediate ACK
```

Расчёт GFS выполняется после ACK.

Async task registry должен:

- держать strong reference на task до завершения;
- логировать exception;
- удалять task после completion;
- позволять graceful shutdown.

Не вводить external queue только ради этого.

## 9. Один процесс на первом этапе

Первый multi-messenger runtime:

```text
gfs-profile-bot.service
  └─ messenger_app.py
      ├─ Telegram polling
      └─ ASGI/FastAPI
          ├─ /webhooks/max
          └─ /webhooks/vk
```

Причина: текущая защита скачивания GRIB — process-local. Общий `.part` при нескольких процессах потенциально конфликтует.

До разделения процессов необходимо:

1. file lock на cache key;
2. уникальная/защищённая работа `.part`;
3. concurrent test двух процессов;
4. проверка SQLite access mode.

## 10. Runtime lifecycle

Предпочтительный вариант:

- ASGI/FastAPI владеет event loop;
- в lifespan запускается `python-telegram-bot` Application через `initialize/start/updater.start_polling`;
- MAX/VK endpoints работают в том же loop;
- shutdown останавливает polling и ждёт активные tasks в разумных пределах.

Не использовать `run_polling()` внутри уже работающего ASGI loop.

## 11. Общий прогнозный flow

### Город без срока

```text
TEXT "Москва"
→ parse location
→ geocoder
→ one candidate
→ save point/session
→ show lead keyboard
→ callback lead
→ ForecastService
```

### Город +24

```text
TEXT "Москва +24"
→ parse location + explicit lead
→ geocoder
→ run immediately
```

### Неоднозначный город

```text
TEXT "Киров"
→ candidates
→ native callback keyboard
→ choose candidate
→ lead
```

### Геолокация

```text
LOCATION
→ GeoPoint
→ remember location
→ lead keyboard
```

## 12. Progress flow

Common service генерирует semantic stages, например:

```text
check_run
select_grid
download
parse
calculate
render
send
```

Renderer отображает их компактно. Platform-specific ограничения не должны менять sequence продукта.

## 13. Ошибки

Common errors разделить по типам:

```text
InputError
LocationNotFound
AmbiguousLocation
RunUnavailable
SourceUnavailable
ProductError
Cancelled
```

Platform client errors не должны протекать внутрь метеоядра.

Пользователь получает короткое понятное сообщение, лог — техническую причину.

## 14. Наблюдаемость

Логировать:

```text
platform
user/chat (без лишних PII)
product
request id
run/lead
stage
duration
result/error class
```

Admin stats получают колонку `platform`.

## 15. Следующий этап

После фиксации документации следующим этапом разработки является создание common contracts/runtime skeleton и шлюзов MAX/VK. Порядок см. `docs/plans/MAX_VK_GATEWAYS_PLAN.md`.
