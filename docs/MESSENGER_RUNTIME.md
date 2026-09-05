# Multi-messenger runtime: Telegram + MAX + VK

Статус: общий runtime является штатным production entrypoint. Реализованы transport/webhook, общие `/profile`, `/aero`, `/windgram`, `/cloudgram` vertical slices и saved recipes для этих продуктов. Остальные продукты последовательно переносятся в messenger-neutral services.

Пошаговая регистрация MAX/VK: [`MESSENGER_REGISTRATION.md`](MESSENGER_REGISTRATION.md).

## Один процесс

```text
systemd → messenger_launcher.py
                 │
                 ├─ Telegram polling
                 ├─ MAX POST /webhooks/max
                 ├─ VK  POST /webhooks/vk
                 └─ FastAPI web/API
```

Runtime остаётся single-process и запускается с `workers=1`. GFS/cache/resource gates являются process-local. Redis, Celery и отдельные очереди не требуются.

`MESSENGER_RUNTIME_ENABLED=1` — production default. `0` — аварийный Telegram-only fallback без смены systemd entrypoint.

## Конфигурация

```env
MESSENGER_RUNTIME_ENABLED=1
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081
MESSENGER_RUNTIME_LOG_LEVEL=info
MESSENGER_RUNTIME_ACCESS_LOG=0
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3

MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=
MAX_WEBHOOK_SECRET=

VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_URL=
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
```

Пустой token отключает соответствующий gateway. Для готовой platform-настройки вручную достаточно:

```text
MAX: token + public HTTPS URL
VK: community token + positive group id + public HTTPS URL
```

Secrets и VK confirmation code подготавливаются `prepare_messenger_config.py`/`setup_messenger_bots.sh`.

## Startup/readiness

`messenger_runtime.py` сначала привязывает общие process resources, затем в lifespan инициализирует Telegram application и polling. Только после успешного `Application.start()` runtime становится ready.

```text
GET /health
GET /ready
```

`/ready` возвращает `503` до полной готовности и `200` после запуска. `/health` показывает включённые платформы, current common products и resource limits без token/secret.

Текущий common product list:

```text
profile
aero
windgram
cloudgram
```

## Shared RuntimeResources

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Один pool используется MAX/VK routers, Telegram handlers, Telegram meteogram и mounted web/API. При `MAX_CONCURRENT_GFS=2` два GFS-расчёта идут суммарно по всем frontend; третий ждёт permit.

Общие `/profile`, `/aero`, `/windgram`, `/cloudgram` services используют тот же GFS gate.

## Production deploy

Fresh install и deploy записывают:

```text
ExecStart=/opt/gfs_profile/.venv/bin/python /opt/gfs_profile/messenger_launcher.py
```

До restart:

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

Для первичной MAX/VK настройки предпочтительный entrypoint:

```bash
sudo bash setup_messenger_bots.sh --max
sudo bash setup_messenger_bots.sh --vk
sudo bash setup_messenger_bots.sh --max --vk
```

Он заранее подготавливает secrets/confirmation code, затем вызывает штатный deploy и проверяет внешнюю регистрацию.

## MAX transport

Production endpoint:

```text
POST /webhooks/max
```

`MessengerWebhookService` проверяет `X-Max-Bot-Api-Secret`, дедуплицирует update и возвращает HTTP 200 до тяжёлого расчёта. `MaxApiClient` использует `https://platform-api2.max.ru`, token только в `Authorization`, retry/backoff для `429`, `5xx`, network errors и upload flow для media.

Registration:

```text
GET existing subscriptions
→ POST /subscriptions URL + secret + update_types
```

Status:

```bash
python register_messenger_webhooks.py --max --status
```

## VK transport

Production endpoint:

```text
POST /webhooks/vk
```

Поддержаны `confirmation`, `message_new`, `message_event`, group/secret validation, callback/location controls, edit status и media upload.

`prepare_messenger_config.py` получает `groups.getCallbackConfirmationCode`; после `/ready` registration script создаёт/обновляет callback server и включает `message_new`/`message_event`.

Status:

```bash
python register_messenger_webhooks.py --vk --status
```

## Общие продукты

### `/profile`

Один `messenger/profile_service.py`: одинаковый run selection, сводка, PNG/CSV и модельная маркировка. MAX/VK поддерживают город/координаты, `Москва +24`, ambiguity, native location, быстрые сроки и пагинацию до `+384`.

### `/aero`

Один `messenger/aero_service.py`: parser, lead validation, actual published run, `aero_product.py`, progress и `CommonProductResult`. Icing/CAT — модельные прокси; GFS не наблюдение и не радиозонд.

### `/windgram`

Default:

```text
ветер
+0…+120 ч
шаг 6 ч
до 500 гПа
```

Параметры: wind/temp/RH, горизонт `120/240/384`, шаг `3/6/12`. Cycle выбирается по максимальному нужному lead.

### `/cloudgram`

Общий `messenger/cloudgram_service.py` использует существующие `cloudgram_product.py` и `cloudgram_render.py`.

Default:

```text
Подробно
+0…+72 ч
шаг 3 ч
```

Параметры:

```text
mode=pro/simple
from/to до +120 ч
step=3/6 ч
```

Common result содержит actual run/cycle UTC, valid range, requested point, GFS grid, max hazard, missing GFS fields и PNG. Hazard/thunder — модельная диагностика, не наблюдавшееся явление.

## Saved recipes

`messenger/user_recipes.py` хранит:

```text
platform + user + product + point + params
```

`run/cycle` и process-local state исключены. Для четырёх common products MAX/VK поддерживают recipe/pin/repeat.

Cloudgram recipe:

```text
from
to
step
mode
point
```

Repeat всегда выбирает свежий подходящий GFS run.

## Следующий продуктовый этап

```text
/map
→ /meteogram
→ /route
→ /settings
→ /schedule
```

Каждый следующий vertical slice использует `RuntimeResources`, common result/progress и `UserRecipeStore`; platform-копия метеорологической логики запрещена.
