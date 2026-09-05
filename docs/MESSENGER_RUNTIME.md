# Multi-messenger runtime: Telegram + MAX + VK

Статус: общий runtime является штатным production entrypoint. Реализованы transport/webhook, общие `/profile` и `/aero` vertical slices и saved recipes для этих продуктов. Остальные продукты последовательно переносятся в messenger-neutral services.

## Один процесс

```text
systemd → messenger_launcher.py
                 │
                 ├─ Telegram polling
                 ├─ MAX POST /webhooks/max
                 ├─ VK  POST /webhooks/vk
                 └─ FastAPI web/API
```

Runtime остаётся single-process и запускается с `workers=1`. Это принципиально: GFS/cache/resource gates являются локальными для процесса. Redis, Celery и отдельные очереди не требуются.

`MESSENGER_RUNTIME_ENABLED=1` — production default. Значение `0` оставлено как аварийный Telegram-only fallback: systemd продолжает запускать `messenger_launcher.py`, а launcher передаёт управление обычному Telegram polling.

## Конфигурация

```env
MESSENGER_RUNTIME_ENABLED=1
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081
MESSENGER_RUNTIME_LOG_LEVEL=info
MESSENGER_RUNTIME_ACCESS_LOG=0
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3

MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
MAX_WEBHOOK_SECRET=

VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
```

Пустой MAX/VK token означает, что соответствующий gateway не создаётся. Telegram и web/API продолжают работать.

Публичный HTTPS завершается Nginx/HAProxy; внутренний `127.0.0.1:8081` напрямую не публикуется.

## Startup/readiness

`messenger_runtime.py` сначала конфигурирует общие process resources, затем в lifespan инициализирует Telegram application и запускает polling. Только после успешного `Application.start()` runtime становится ready.

```text
GET /health
```

возвращает состояние процесса, включённые платформы и лимиты ресурсов.

```text
GET /ready
```

возвращает `503` до полной готовности Telegram runtime и `200` после запуска. Deploy ждёт именно `/ready`, а не просто открытый TCP-порт.

## Shared RuntimeResources

`messenger/runtime_resources.py` создаёт единый process-wide pool:

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Он используется:

- общими MAX/VK routers;
- legacy Telegram handlers через совместимые globals `GFS_SEMAPHORE`/`GEOCODE_SEMAPHORE`;
- Telegram meteogram;
- mounted web/API для GFS profile;
- последующими common product services.

Смысл лимита process-wide. При `MAX_CONCURRENT_GFS=2` два расчёта могут идти одновременно независимо от источника запросов; третий Telegram/MAX/VK/web запрос ждёт освобождения permit.

Для blocking geocode/web calls используется тот же underlying threading gate, а async handlers получают неблокирующий async context adapter. Поэтому нельзя случайно получить отдельные лимиты «2 для Telegram + 2 для MAX/VK».

## Production deploy

Fresh install и `deploy_telegram_bot.sh` записывают:

```text
ExecStart=/opt/gfs_profile/.venv/bin/python /opt/gfs_profile/messenger_launcher.py
```

Старый Telegram-only unit автоматически мигрируется при следующем deploy.

До restart выполняются:

```text
runtime_check.py
messenger_config_check.py
geocoder_preflight.py
```

После restart:

```text
GET /ready
register_telegram_commands.py
register_messenger_webhooks.py
```

Webhook не регистрируется на ещё не запущенный endpoint.

`install_messenger_runtime.sh` сохранён как аварийный ручной переключатель, но для обычного install/deploy больше не требуется.

Подробно: `DEPLOY.md`.

## MAX transport

MAX production использует:

```text
POST /webhooks/max
```

`MessengerWebhookService` проверяет `X-Max-Bot-Api-Secret`, дедуплицирует входящие события и возвращает HTTP 200 до тяжёлого GFS-расчёта. Расчёт выполняется фоновой asyncio task того же процесса; status message редактируется через gateway.

`MaxApiClient` использует `https://platform-api2.max.ru`, передаёт token только через `Authorization`, поддерживает retry/backoff для `429`, `5xx` и network errors и выполняет `/uploads → attachment → /messages` для media.

## VK transport

VK Callback API использует:

```text
POST /webhooks/vk
```

Поддержаны confirmation, secret/group validation, `message_new`, `message_event`, callback/location controls, edit status, PNG/file upload.

## Общие продукты

### `/profile`

Telegram/MAX/VK используют общий `messenger/profile_service.py`: одинаковый run selection, profile result, PNG/CSV и модельная маркировка.

MAX/VK flow поддерживает город/координаты, `Москва +24`, ambiguous city, native location, быстрые сроки, пагинацию до `+384`, `/status`, `/cancel` и редактируемый progress.

### `/aero`

Telegram/MAX/VK используют `messenger/aero_service.py`: один parser, lead validation, фактический опубликованный GFS run, `aero_product.py`, progress contract и `CommonProductResult`.

Результат показывает run/cycle UTC, lead, valid UTC, requested point и GFS grid point. Icing/CAT обозначаются как модельные прокси; GFS не называется наблюдением или радиозондом.

Подробно: `docs/MESSENGER_AERO_SERVICE.md`.

## Saved recipes

`messenger/user_recipes.py` хранит сценарии по:

```text
platform + user + product + point + params
```

`run/cycle` и process-local state исключены. Repeat всегда выбирает новый подходящий model run.

Для `/profile` и `/aero` MAX/VK уже поддерживают recipe/pin/repeat. Telegram использует тот же логический контракт в персональном UX.

## Следующий продуктовый этап

После production runtime следующий vertical slice:

```text
/windgram
→ /cloudgram
→ /map
→ /meteogram
→ /route
→ /settings
→ /schedule
```

Каждый продукт сразу должен использовать `RuntimeResources`, общий result/progress contract и `UserRecipeStore`; платформенная копия метеорологической логики запрещена.
