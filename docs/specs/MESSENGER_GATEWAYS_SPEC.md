# SPEC: Messenger Gateways для Telegram / MAX / VK

Статус: approved for implementation planning.
Дата: 2026-09-04.
Рабочая ветка: `telegram-bot`.

## 1. Назначение

Спецификация определяет единый контракт между transport adapters и метеорологическим сервисом `gfs_profile`.

Цель — обеспечить одинаковые прогнозные сценарии и одинаковую метеорологическую продукцию в Telegram, MAX и VK без дублирования расчётов.

## 2. Scope первого релиза gateways

Обязательно:

```text
/start
/help
/status
/cancel
/profile
/aero
/windgram
/cloudgram
/meteogram
/map
```

Вход:

- город;
- координаты;
- native location;
- команды;
- callback buttons.

Выход:

- текст;
- PNG;
- CSV;
- DOCX/PDF, если продукт их уже поддерживает;
- MP4/GIF/серия PNG для карты, если поддерживается common product result.

Не входит в первый gateway milestone:

- полный перенос `/route`;
- полный перенос `/schedule`;
- полный перенос `/settings`/персонального UX.

Но API/state design не должен блокировать их последующее подключение.

## 3. Functional parity

### F-001 Start

`START` или `/start` возвращает одинаковый набор основных продуктов и короткую маркировку GFS как модели.

### F-002 Plain city

`Москва`:

1. geocoder;
2. если один кандидат — сохранить точку в session;
3. показать lead picker;
4. после lead callback запустить профиль.

### F-003 Explicit lead

`Москва +24` сразу запускает профиль после однозначного geocode.

### F-004 Ambiguous city

При нескольких кандидатах platform renderer показывает native callback buttons. Выбор восстанавливает point и продолжает flow.

### F-005 Location

Native location нормализуется в `lat/lon`, затем показывает lead picker.

### F-006 Lead picker

Первая страница содержит быстрые сроки:

```text
+0 +3 +6 +12 +24 +48
```

Все canonical leads до +384 доступны пагинацией.

### F-007 Status

`/status` использует common run availability service, а не platform-specific проверку.

### F-008 Cancel

`/cancel` очищает только ephemeral flow. Persistent point/preferences не удаляются.

### F-009 Product result

Один и тот же ProductRequest при фиксированных входных данных обязан формировать одинаковый CommonProductResult для всех платформ.

## 4. Input contract

Предлагаемый Python contract:

```python
@dataclass(frozen=True)
class Location:
    lat: float
    lon: float

@dataclass(frozen=True)
class NormalizedEvent:
    platform: str
    raw_event_id: str | None
    event_type: str
    user_id: str
    chat_id: str
    message_id: str | None = None
    text: str | None = None
    command: str | None = None
    callback_payload: str | None = None
    location: Location | None = None
    timestamp: float | None = None
```

Platform ids хранятся как строки в common contract, чтобы не зависеть от числового диапазона/формата конкретного API.

## 5. Output gateway contract

```python
class MessengerGateway(Protocol):
    async def send_text(...): ...
    async def edit_text(...): ...
    async def send_image(...): ...
    async def send_file(...): ...
    async def send_animation(...): ...
    async def answer_callback(...): ...
```

Каждый метод возвращает lightweight platform handle, если он нужен следующему действию.

Common service не импортирует Telegram/MAX/VK SDK classes.

## 6. Button contract

```python
@dataclass(frozen=True)
class UiButton:
    text: str
    action: str
    payload: str | None = None
    url: str | None = None

@dataclass(frozen=True)
class UiKeyboard:
    rows: tuple[tuple[UiButton, ...], ...]
```

Allowed actions:

```text
callback
request_location
text
link
```

Renderer обязан валидировать platform limits до отправки.

## 7. Callback codec

Callback должен иметь version marker.

Пример логического payload:

```text
v1|profile|lead|24
v1|place|2
v1|leadpage|1
v1|product|map
```

Не фиксировать этот формат как обязательный wire-format до проверки лимитов MAX/VK/Telegram. Требования:

- deterministic encode/decode;
- whitelist actions;
- validation integer/range;
- no secrets;
- no Python object ids;
- возможность продолжить flow после restart, если данные устойчиво сохранены;
- stale callback даёт понятный ответ и предлагает начать заново.

## 8. Common product request

Минимальный request:

```python
@dataclass(frozen=True)
class ProductRequest:
    product: str
    point: GeoPoint
    lead_from: int | None = None
    lead_to: int | None = None
    step: int | None = None
    run: GfsRun | None = None
    params: Mapping[str, object] = field(default_factory=dict)
```

Explicit run разрешён для repeat/debug flow, но обычный пользовательский запрос выбирает актуальный опубликованный run через common selector.

## 9. Common result

```python
@dataclass
class ProductAttachment:
    kind: str
    path: Path
    filename: str
    caption: str = ""
    mime_type: str | None = None

@dataclass
class CommonProductResult:
    product: str
    summary: str
    attachments: list[ProductAttachment]
    metadata: dict[str, object]
    repeat_command: str | None = None
    actions: list[UiButton] = field(default_factory=list)
```

`metadata` для GFS-native продукта минимум:

```text
model
data_kind=model
run_date
run_cycle
lead(s)
valid_utc
requested_lat/lon
grid_lat/lon
source/provider
```

## 10. Progress contract

Метеоядро и продуктовые функции должны генерировать semantic progress events без Telegram wording.

```python
ProgressEvent(
    stage="download",
    current=...,
    total=...,
    message="...",
)
```

Common progress formatter превращает event в короткий текст, renderer только отправляет/edit.

Status update throttling находится в messenger service/gateway policy, а не в GFS core.

## 11. Error contract

Common exceptions:

```text
MessengerInputError
LocationNotFoundError
AmbiguousLocationError
RunUnavailableError
ProductUnavailableError
ProductExecutionError
CancelledError
```

Platform errors:

```text
PlatformAuthError
PlatformRateLimitError
PlatformTemporaryError
PlatformPermanentError
```

Platform exception не должен превращаться в `GfsProfileError`.

## 12. MAX transport requirements

### Endpoint

```text
POST /webhooks/max
```

### Validation

- HTTPS termination outside app/reverse proxy;
- verify `X-Max-Bot-Api-Secret`;
- token only outbound in `Authorization`;
- dedupe by update identity where available.

### Updates

```text
bot_started → START
message_created → TEXT/COMMAND/LOCATION
message_callback → CALLBACK
```

### API

Base URL:

```text
https://platform-api2.max.ru
```

Required capabilities:

```text
POST messages
PUT messages
POST callback answer
POST uploads
POST/GET/DELETE subscriptions for setup/diagnostics
```

Production notification mode: Webhook.

Media image type: `image`.

Retry: `429`, `5xx`, timeout/network. Bounded exponential backoff + jitter; no infinite retry.

## 13. VK transport requirements

### Endpoint

```text
POST /webhooks/vk
```

### Validation

- group id;
- callback secret when configured;
- `confirmation` handled synchronously;
- dedupe working events.

### Updates

```text
message_new → TEXT/COMMAND/LOCATION
message_event → CALLBACK
```

### Required VK API methods

```text
messages.send
messages.edit
messages.sendMessageEventAnswer
photos.getMessagesUploadServer
photos.saveMessagesPhoto
docs.getMessagesUploadServer
docs.save
```

`messages.send` uses unique `random_id`.

API version supplied via config.

## 14. Telegram migration requirements

Telegram remains regression reference.

Migration sequence:

1. Preserve existing commands/handlers.
2. Extract pure parser/service/formatter from Telegram-specific module.
3. Adapt Telegram handler to common service.
4. Run existing Telegram tests.
5. Only then reuse service from MAX/VK.

Не переписывать весь Telegram bot одним big-bang refactor.

## 15. Runtime specification

Первый runtime — один process.

```text
messenger_app.py
  FastAPI/ASGI
  Telegram Application polling lifecycle
  MAX webhook
  VK webhook
  async task registry
```

ASGI lifespan:

```text
startup:
  init gateways
  start Telegram application
  start Telegram polling

shutdown:
  stop accepting new internal tasks
  stop Telegram updater
  stop Telegram application
  cancel/await task registry safely
```

## 16. Security

- secrets only `.env`;
- never log tokens/secrets;
- mask token in deploy diagnostics;
- verify MAX/VK webhook secrets before parsing business payload deeply;
- limit request body size at reverse proxy/app;
- validate callback payload;
- do not fetch arbitrary user-provided URLs for attachments;
- sanitize filenames/captions.

## 17. Cache concurrency

До multi-process deployment:

- один process only;
- общий semaphore GFS;
- shared `.cache_gfs`.

Отдельная будущая task: заменить process-local cache locking межпроцессным lock.

Acceptance test для будущего split-process: два процесса одновременно запрашивают один cache key, в результате существует один валидный GRIB2, нет повреждённого `.part`, оба запроса завершаются корректно.

## 18. Persistence

Preferences schema должна поддержать:

```text
platform TEXT NOT NULL
user_id TEXT NOT NULL
```

Unique key — `(platform, user_id)`.

Session state может быть in-memory на первом этапе. Persistent defaults — SQLite.

Admin/request stats получают `platform`.

## 19. Testing contract

### Common tests

Один набор сценариев запускается против fake gateway implementations:

```text
start
city
city +24
ambiguous city
location
lead callback
pagination
cancel
profile result
```

Проверять не platform markup, а последовательность normalized actions/common result.

### Platform adapter tests

Telegram — существующие tests + новые contract bridges.

MAX — secret/events/callback/geo/edit/upload/retry/dedupe.

VK — confirmation/secret/events/location/edit/uploads/random_id/retry/dedupe.

### Weather tests

Существующие GFS +24/+384 smoke остаются обязательными.

## 20. Acceptance criteria первого gateway milestone

Сценарий:

```text
Москва +24
```

в Telegram/MAX/VK должен приводить к одному common ProfileResult с одинаковыми run/lead/grid/values и отличаться только платформенной упаковкой сообщения/файлов.

Сценарий location → +48 обязан работать на всех трёх платформах.

Неоднозначный город должен решаться native buttons без повторного geocoder после выбора.

Webhook endpoints не блокируются GFS calculation.

После restart stale callback не вызывает traceback и предлагает начать flow заново.

## 21. Definition of Done

Gateway milestone готов, когда:

- common contracts существуют и покрыты tests;
- Telegram профиль переведён на common service без UX regression;
- MAX и VK проходят базовый profile flow;
- PNG/CSV доставляются;
- callback/location/status edit работают;
- GFS source/run маркируются одинаково;
- tests и GFS smoke проходят;
- deploy/env/docs соответствуют коду;
- нет копии метеорологических вычислений в platform adapters.
