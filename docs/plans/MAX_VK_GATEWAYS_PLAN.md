# PLAN: реализация шлюзов MAX и VK

Дата: 2026-09-04.
Рабочая ветка: `telegram-bot`.

## Цель следующего этапа

Следующий этап разработки после этой документации — не «написать два новых бота», а построить общий messenger gateway layer и подключить к нему MAX и VK.

Главный критерий успеха первого этапа разработки: один профильный use-case работает через Telegram, MAX и VK и формирует один и тот же common result.

## Этап 0. Зафиксированная архитектура

Готово документально:

- `AGENTS.md`;
- `MAX_BOT.md`;
- `VK_BOT.md`;
- `docs/MESSENGER_ARCHITECTURE.md`;
- `docs/specs/MESSENGER_GATEWAYS_SPEC.md`;
- данный план.

Перед кодом повторно проверить актуальный HEAD ветки и MAX/VK API changelog.

## Этап 1. Common contracts

Создать минимальный framework без изменения поведения Telegram:

```text
messenger/contracts.py
messenger/events.py
messenger/state.py
messenger/router.py
messenger/service.py
```

Минимальные типы:

```text
NormalizedEvent
Location
UiButton
UiKeyboard
StatusHandle
ProductRequest
ProductAttachment
CommonProductResult
MessengerGateway protocol
```

Добавить pure unit tests для encode/decode callback, event validation и common UI models.

Не переносить все продукты сразу.

## Этап 2. Пилот: общий profile service

Выделить из `telegram_bot_core.py` профильный flow:

```text
parse request
→ geocode
→ candidate decision
→ lead decision
→ run selection
→ build_profile
→ formatter
→ PNG/CSV
→ common result
```

Telegram handler остаётся platform adapter и вызывает common profile service.

Обязательный контроль:

- существующие Telegram flow tests не ломаются;
- `/profile Москва +24` выдаёт тот же результат;
- `Москва` → сроки работает;
- location работает;
- ambiguous city работает;
- run selection не переезжает в renderer.

Это самый важный архитектурный gate. MAX/VK нельзя подключать напрямую к старым `telegram_*` runners.

## Этап 3. Common runtime skeleton

Создать `messenger_app.py` или эквивалент.

Первый runtime:

```text
один process
├─ Telegram polling
└─ FastAPI/ASGI
   ├─ POST /webhooks/max
   ├─ POST /webhooks/vk
   └─ GET /healthz
```

В ASGI lifespan:

- initialize/start PTB Application;
- start updater polling без `run_polling()`;
- initialize MAX/VK clients;
- создать task registry;
- корректный shutdown.

Добавить unit test lifecycle без реальных сетевых запросов.

## Этап 4. MAX API client

Реализовать небольшой async client:

```text
get_me
create/list/delete subscription (setup/diagnostics)
send_message
edit_message
answer_callback
request_upload
upload_file
send attachment
```

Обязательно:

- base `platform-api2.max.ru`;
- `Authorization` header;
- timeout;
- 429/5xx/network retry;
- bounded backoff + jitter;
- JSON/API error mapping;
- не логировать token;
- `image`, не `photo`.

Client tests — через mocked HTTP transport.

## Этап 5. MAX webhook adapter

Реализовать:

```text
POST /webhooks/max
```

Порядок:

1. verify `X-Max-Bot-Api-Secret`;
2. parse update;
3. dedupe;
4. map `bot_started/message_created/message_callback`;
5. `message_callback` быстро answer;
6. register async processing task;
7. HTTP ACK.

MAX renderer:

- text;
- edit status;
- inline callback buttons;
- `request_geo_location`;
- PNG image;
- CSV file.

## Этап 6. MAX profile vertical slice

Реализовать полный пользовательский путь:

```text
/start
Москва
Москва +24
ambiguous city
location
lead buttons
pagination
/status
/cancel
/profile
PNG
CSV
```

После этого провести contract comparison Telegram vs MAX на одном fake ProfileResult.

Не переходить к другим продуктам, пока этот vertical slice не стабилен.

## Этап 7. VK API client

Реализовать небольшой async VK client.

Минимум:

```text
messages.send
messages.edit
messages.sendMessageEventAnswer
photos.getMessagesUploadServer
photos.saveMessagesPhoto
docs.getMessagesUploadServer
docs.save
```

Обязательно:

- `VK_API_VERSION` из config;
- group token;
- unique `random_id`;
- timeout;
- retry temporary errors;
- API error mapping;
- upload helpers.

## Этап 8. VK Callback adapter

Endpoint:

```text
POST /webhooks/vk
```

Порядок:

1. parse minimal envelope;
2. verify group id/secret;
3. `confirmation` вернуть синхронно;
4. dedupe;
5. map `message_new`;
6. map `message_event`;
7. answer callback event;
8. register async task;
9. ACK.

Renderer:

- keyboard;
- location action;
- edit status;
- photo upload;
- document upload.

## Этап 9. VK profile vertical slice

Повторить тот же contract:

```text
/start
Москва
Москва +24
ambiguous city
location
lead buttons
pagination
/status
/cancel
/profile
PNG
CSV
```

После этого один common profile scenario должен проходить Telegram/MAX/VK contract tests.

## Этап 10. Остальные продукты

Переносить последовательно, по одному продукту:

1. `/aero`;
2. `/windgram`;
3. `/cloudgram`;
4. `/meteogram`;
5. `/map`.

Для каждого:

```text
extract common orchestration
→ Telegram regression
→ MAX adapter/render
→ VK adapter/render
→ contract tests
→ docs
```

Не делать пять параллельных копий до стабилизации общего шаблона.

## Этап 11. Persistent preferences/statistics

После базового parity:

- перейти от Telegram-specific preferences к общей schema;
- ключ `(platform, user_id)`;
- статистика request/user получает `platform`;
- миграция существующих Telegram rows без потери данных;
- `.cache_gfs` сохраняется deploy.

Только после этого переносить `/settings`.

## Этап 12. Route / Schedule

### Route

Перенести общий route request/result и renderer attachment flow.

### Schedule

Schedule destination должен стать platform-neutral:

```text
platform
chat_id
user_id
product snapshot
schedule
```

Scheduler вызывает common product service и platform gateway по destination.

Не создавать отдельный scheduler MAX/VK.

## Этап 13. Deploy

Переработать install/deploy только после рабочего runtime.

Цель:

```text
/opt/gfs_profile
.env
.venv
.cache_gfs
.install-state
```

один systemd service для первого multi-messenger runtime.

Добавить env:

```env
MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=
MAX_WEBHOOK_SECRET=
MAX_API_BASE=https://platform-api2.max.ru

VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=

MESSENGER_WEBHOOK_HOST=127.0.0.1
MESSENGER_WEBHOOK_PORT=8081
```

Deploy:

1. сохраняет `.env/.cache_gfs/.venv/.install-state` как сейчас;
2. проверяет runtime imports;
3. запускает unit/contract tests;
4. restart только после успешных проверок;
5. проверяет healthz;
6. регистрирует/проверяет MAX webhook отдельно, без раскрытия secret;
7. VK callback endpoint конфигурируется в панели/API сообщества по документированной инструкции.

## Этап 14. Reverse proxy

Production endpoint:

```text
https://<host>/webhooks/max
https://<host>/webhooks/vk
```

Reverse proxy:

- TLS;
- request size limit;
- proxy timeout только для webhook ACK, не для GFS calculation;
- local upstream `127.0.0.1:<port>`;
- логирование без secrets.

Для MAX соблюдать текущие требования trusted TLS/HTTPS.

## Этап 15. Cache lock перед multi-process

Не является блокером первого one-process release.

До разделения transports на разные systemd services:

- межпроцессный lock cache key;
- безопасный `.part` lifecycle;
- concurrent test;
- rollback corrupted cache.

Пока этого нет, разные messenger processes с общей `.cache_gfs` запрещены.

## Test matrix

### Common

```text
start
city
city+lead
ambiguous
location
lead page
cancel
status
profile result
```

### MAX

```text
secret
bot_started
message_created
message_callback
request_geo_location
edit status
image upload
file upload
429/5xx/network retry
dedupe
```

### VK

```text
confirmation
group/secret
message_new
message_event
location
edit status
photo upload
doc upload
random_id
retry
dedupe
```

### GFS

```bash
python -m gfs_core --lat 45.0355 --lon 38.9753 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
```

## Commit strategy

Рекомендуемые атомарные commits:

```text
refactor: выделить common messenger contracts
refactor: вынести профиль в общий service
feat: добавить runtime messenger gateway
feat: добавить MAX API client
feat: добавить MAX webhook profile flow
feat: добавить VK API client
feat: добавить VK callback profile flow
refactor: перенести aero в common service
...
test: проверить паритет Telegram MAX VK
docs: описать multi-messenger deploy
```

Не делать один огромный commit «добавить MAX и VK».

## Milestone 1 Definition of Done

Первый milestone gateways завершён, когда:

- Telegram не потерял существующий profile UX;
- MAX и VK принимают город/координаты/location;
- callback lead picker работает;
- `/profile` работает на всех трёх;
- actual run выбирает common service;
- summary/PNG/CSV одинаковы по содержанию;
- webhook быстро ACK;
- status редактируется без spam;
- tests проходят;
- GFS +24/+384 smoke проходят;
- secrets/config documented;
- deploy plan готов к включению runtime.

Следующий milestone после этого — перенос `/aero`, `/windgram`, `/cloudgram`, `/meteogram`, `/map`.
