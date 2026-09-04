# MAX Bot — текущее состояние и эксплуатация

Статус на 2026-09-04: transport/webhook, общие `/profile` и `/aero` vertical slices и сохранённые сценарии этих продуктов реализованы в рабочей ветке `telegram-bot`. Остальные продукты последовательно переносятся в общий messenger service; отдельной метеорологической логики MAX не содержит.

## Архитектура

```text
MAX Update
→ MAX adapter
→ NormalizedEvent
→ common messenger router/service
→ CommonProductResult
→ MAX gateway/renderer
```

Метеорологические расчёты, geocoder, выбор GFS run и formatter не копируются в MAX.

## Официальный API

Актуальный endpoint:

```text
https://platform-api2.max.ru
```

Токен передаётся заголовком `Authorization`. Production использует Webhook через `POST /subscriptions`; при активной подписке Long Polling не используется. Webhook должен быть HTTPS с доверенным TLS, а при указанном `secret` проверяется `X-Max-Bot-Api-Secret`.

Основные источники:

- https://dev.max.ru/docs-api
- https://dev.max.ru/docs-api/methods/POST/subscriptions
- https://dev.max.ru/docs-api/objects/Update
- https://dev.max.ru/docs-api/changelog-api

Перед существенным изменением transport/client повторно сверять reference и changelog.

## Поддерживаемые события

```text
bot_started
message_created
message_callback
```

Adapter нормализует текст, команды, location, callback payload, user/chat/message ids и platform event id. Webhook быстро валидирует запрос и возвращает `200`, а GFS-расчёт выполняется асинхронной задачей.

## Кнопки и callback

Renderer поддерживает native callback и `request_geo_location`. Callback payload versioned и не зависит только от RAM-state.

Saved recipe callbacks используют устойчивый `recipe_id`:

```text
v1|recipe|run|<id>
v1|recipe|toggle|<id>
v1|recipe|change|<id>
```

Поэтому repeat/pin работают после restart процесса.

## Реализованный `/profile`

Через общий service работают:

- `/profile`;
- `Москва` → выбор срока;
- `Москва +24` → немедленный расчёт;
- неоднозначный город;
- native location;
- быстрые `+0,+3,+6,+12,+24,+48`;
- все сроки до `+384`;
- одно редактируемое status message;
- общий GFS run selection;
- одинаковая сводка, PNG и CSV.

## Реализованный `/aero`

MAX использует тот же `messenger/aero_service.py`, что Telegram и VK. Метеорологическое ядро остаётся в существующих `aero_product.py`/`diagnostic_profile`.

Flow:

```text
/aero Москва +24
→ сразу Skew-T

/aero
→ город / координаты / геолокация
→ неоднозначность при необходимости
→ срок
→ Skew-T
```

Поддерживаются быстрые сроки `+0,+3,+6,+12,+24,+48` и все сроки до `+384` с пагинацией.

Результат содержит:

- фактический GFS run/cycle UTC;
- lead и valid UTC;
- requested point;
- GFS grid point;
- Skew-T log-P и годограф;
- PNG;
- явную маркировку GFS как модели;
- icing/CAT только как модельные прокси.

Тип диаграммы пользователь не выбирает: `/aero` всегда означает Skew-T log-P с годографом.

Подробно: `docs/MESSENGER_AERO_SERVICE.md` и `docs/AERO_DIAGRAM.md`.

## Сохранённые сценарии

Messenger-neutral SQLite:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Для `/profile` и `/aero` успешный расчёт сохраняет точку и параметры, но не `run/cycle`. `/start` показывает до двух быстрых сценариев. Команда без аргументов открывает закреплённый или последний успешный вариант. Repeat передаёт `run=None`, поэтому выбирается свежий опубликованный цикл.

Хранилище изолировано по `platform + user_id`, поэтому MAX и VK не смешивают пользовательское состояние.

## Media

Client/gateway использует схему:

```text
POST /uploads
→ upload URL/token
→ загрузка файла
→ POST /messages с attachment
```

PNG отправляется как image attachment; файловые продукты используют file attachment. Retry/backoff применяется к `429`, `5xx` и network errors.

## Конфигурация

```env
MESSENGER_RUNTIME_ENABLED=1
MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
MAX_WEBHOOK_SECRET=
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Runtime слушает loopback; публичный HTTPS завершается Nginx/HAProxy. MAX и Telegram работают в одном Python process, без Redis/Celery.

## Следующий этап паритета

Следующий vertical slice — `/windgram`, затем:

```text
/cloudgram
/meteogram
/map
/route
/settings
/schedule
```

Каждый новый продукт должен использовать общий result contract, progress contract и `UserRecipeStore`. MAX-копии GFS/geocoder/product logic запрещены.
