# Multi-messenger runtime: Telegram + MAX + VK

Общий runtime — штатный production entrypoint. Telegram, MAX и VK используют одно метеорологическое ядро и общие product services, но жизненный цикл каждой платформы изолирован: ошибка одной платформы не должна останавливать остальные или web/API.

Пошаговая регистрация MAX/VK: [`MESSENGER_REGISTRATION.md`](MESSENGER_REGISTRATION.md).

## Один процесс, независимые платформы

```text
systemd → messenger_launcher.py
                 │
                 ├─ Telegram polling ──┐
                 ├─ MAX webhook ───────┼→ common services / RuntimeResources
                 ├─ VK webhook ────────┤
                 └─ FastAPI web/API ───┘
```

Runtime остаётся single-process (`workers=1`), без Redis/Celery. Общие GFS/cache/resource gates process-local, а provider lifecycle — независимый.

## Переключатели платформ

```env
TELEGRAM_ENABLED=auto
MAX_ENABLED=auto
VK_ENABLED=auto
```

Значения:

```text
auto  включить платформу только если задан token
1     платформа явно должна работать; ошибка конфигурации → degraded
0     карантин платформы; её старые token/secret могут остаться в .env
```

Пример: Telegram и MAX работают, а VK временно сломан:

```env
TELEGRAM_ENABLED=auto
MAX_ENABLED=auto
VK_ENABLED=0
```

Перезапуск не требует удалять `VK_*`. Telegram/MAX продолжают работать.

## Состояния

У каждой платформы независимое состояние:

```text
ready       локальная конфигурация валидна, adapter/gateway запущен
degraded    платформа запрошена, но её конфигурация/startup неисправны
off         платформа отключена
```

`GET /health` возвращает как совместимые booleans, так и подробный `platform_status`. Например:

```json
{
  "status": "degraded",
  "platforms": {"telegram": true, "max": true, "vk": false},
  "platform_status": {
    "telegram": {"state": "ready", "ready": true},
    "max": {"state": "ready", "ready": true},
    "vk": {"state": "degraded", "ready": false, "reason": "..."}
  }
}
```

Общий `status=degraded` является диагностикой, а не отказом runtime.

## Readiness

`GET /ready` проверяет готовность общей HTTP/runtime инфраструктуры. Он не становится `503` только потому, что один provider degraded.

Поэтому сценарий:

```text
Telegram = ready
MAX      = ready
VK       = degraded
```

означает:

```text
/ready → 200
Telegram работает
MAX работает
/webhooks/vk → 503 только для VK
/web и API работают
```

До запуска самого FastAPI lifespan `/ready` возвращает `503`.

## Telegram fault isolation

Telegram больше не является hard dependency FastAPI startup. Ошибка `initialize/getMe/start_polling/Application.start`:

```text
→ логируется
→ Telegram получает state=degraded
→ частично созданный Telegram application очищается
→ MAX/VK/web продолжают работать
```

Shutdown Telegram также не должен срывать общий shutdown.

## MAX/VK fault isolation

`MessengerWebhookService.from_env()` создаёт gateway только для локально `ready` платформы. Невалидный VK не препятствует созданию MAX gateway и наоборот.

Webhook сломанной/отключённой платформы получает свой `503`; соседние endpoints остаются доступны. Provider task exception логируется только для соответствующего event-task и не завершает процесс.

## Конфигурационный preflight

```bash
python messenger_config_check.py
```

по умолчанию диагностический и best-effort: runtime host/port ошибки fatal, а platform errors показываются как `degraded`.

Строгая проверка только выбранной платформы:

```bash
python messenger_config_check.py --strict-telegram
python messenger_config_check.py --strict-max
python messenger_config_check.py --strict-vk
```

Это используется мастером настройки конкретного provider и не затрагивает соседей.

## Регистрация provider

Обычный deploy выполняет:

```bash
python register_messenger_webhooks.py
```

MAX и VK обрабатываются независимо. Если, например, VK API недоступен, script сообщает `ERROR VK`, но не отменяет успешно зарегистрированный MAX и не делает общий deploy неуспешным.

Строгая диагностика:

```bash
python register_messenger_webhooks.py --status --max
python register_messenger_webhooks.py --status --vk
```

возвращает ненулевой код, если именно выбранная платформа не работает.

`setup_messenger_bots.sh --max` подготавливает и проверяет только MAX; битые старые `VK_*` не читаются. Аналогично для `--vk`.

## MAX transport

Production endpoint:

```text
POST /webhooks/max
```

MAX Webhook должен иметь публичный HTTPS endpoint на 443. `X-Max-Bot-Api-Secret` проверяется до обработки, update дедуплицируется, HTTP 200 возвращается быстро, а GFS расчёт идёт отдельной asyncio task.

## VK transport

Production endpoint:

```text
POST /webhooks/vk
```

Поддержаны `confirmation`, `message_new`, `message_event`, group/secret validation, callback/location controls и media. Ошибка VK API влияет только на VK event/setup.

## Shared RuntimeResources

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Один pool используется Telegram/MAX/VK/web. Изоляция platform lifecycle не означает отдельные тяжёлые квоты: серверные лимиты остаются общими.

## Общие продукты

На текущем этапе common services:

```text
/profile
/aero
/windgram
/cloudgram
```

Они используют одинаковые run selection, formatter/result contract и saved recipes. `run/cycle` в recipe не сохраняются.

Следующие vertical slices:

```text
/map
/meteogram
/route
/settings
/schedule
```

Каждый из них должен наследовать этот fault-isolation contract: platform adapter может отказать, но common service и остальные adapters продолжают работать.
