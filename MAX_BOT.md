# MAX Bot — текущее состояние и эксплуатация

Статус на 2026-09-04: transport/webhook, общий `/profile` vertical slice и сохранённые profile-сценарии реализованы в ветке `telegram-bot`. Остальные продукты последовательно переносятся в общий messenger service; отдельной метеорологической логики MAX не содержит.

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

Токен передаётся заголовком `Authorization`. Production использует Webhook через `POST /subscriptions`; при активной подписке Long Polling не работает. Webhook должен быть HTTPS с доверенным TLS, а при указанном `secret` проверяется `X-Max-Bot-Api-Secret`.

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

Для сохранённых сценариев используется устойчивый `recipe_id`:

```text
v1|recipe|run|<id>
v1|recipe|toggle|<id>
v1|recipe|change|<id>
```

Поэтому повтор/закрепление работает после рестарта процесса и не требует живого wizard state.

## Реализованный `/profile` flow

В MAX через общий service работают:

- `/start`;
- `/profile`;
- `Москва` → выбор срока;
- `Москва +24` → немедленный расчёт;
- неоднозначный город → inline/callback выбор;
- location → выбор срока;
- быстрые `+0,+3,+6,+12,+24,+48`;
- все сроки до `+384` с пагинацией;
- `/status`, `/cancel`;
- одно редактируемое status message;
- актуальный GFS run для требуемого lead;
- общая сводка, PNG и CSV.

## Сохранённые profile-сценарии

Успешный профиль сохраняется в messenger-neutral SQLite:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Сценарий содержит точку и срок, но не `run/cycle`. `/start` показывает до двух быстрых profile recipes. `/profile` без аргументов открывает последний закреплённый сценарий, иначе последний успешный. Повтор передаёт `run=None`, поэтому выбирается свежий опубликованный цикл.

Можно закреплять/откреплять сценарии; callbacks stateless по `recipe_id`. Хранилище изолировано по `platform + user_id`, поэтому MAX и VK не смешивают пользовательское состояние.

Подробно: `docs/MESSENGER_SAVED_RECIPES.md`.

## Media

Client/gateway использует схему:

```text
POST /uploads
→ upload URL/token
→ загрузка файла
→ POST /messages с attachment
```

Поддерживаются PNG/CSV и animation/video transport по возможностям gateway. Retry/backoff применяется к `429`, `5xx` и network errors.

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

В общий service последовательно переносятся:

```text
/aero
/windgram
/cloudgram
/meteogram
/map
/route
/settings
/schedule
```

Каждый новый продукт должен сразу использовать общий result contract, progress contract и `UserRecipeStore`. Telegram-only копирование бизнес-логики запрещено.
