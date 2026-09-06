# Multi-messenger runtime: Telegram + MAX + VK

Общий runtime — штатный production entrypoint. Все семь product services, settings/recipes и schedules доступны при независимом lifecycle каждой платформы.

См. также:

- [`MESSENGER_REGISTRATION.md`](MESSENGER_REGISTRATION.md) — подключение MAX/VK;
- [`MESSENGER_PARITY.md`](MESSENGER_PARITY.md) — итоговая матрица;
- [`MESSENGER_SCHEDULES.md`](MESSENGER_SCHEDULES.md) — automatic delivery.

## Один процесс

```text
systemd → messenger_launcher.py
                 │
                 ├─ Telegram polling ──┐
                 ├─ MAX webhook ───────┼→ common routers/services
                 ├─ VK webhook ────────┤
                 ├─ common scheduler ──┤
                 └─ web/API ───────────┘
                              ↓
                       RuntimeResources
```

`workers=1`. Redis/Celery не нужны.

## Независимый lifecycle

```env
TELEGRAM_ENABLED=auto
MAX_ENABLED=auto
VK_ENABLED=auto
```

```text
auto  включить при наличии/валидности локальной конфигурации
1     явно запросить; ошибка → degraded
0     отключить только эту платформу
```

Статусы:

```text
ready
degraded
off
```

`GET /health` показывает их отдельно. Общий `status=degraded` — диагностика, а не остановка runtime.

Пример:

```text
Telegram ready
MAX      ready
VK       degraded
```

Результат:

```text
/ready            200
Telegram           работает
MAX                работает
/webhooks/vk       локальный 503
web/API             работает
MAX schedules       работают
VK schedules        получают локальный error и будущий next_run
```

## `/ready` и `/health`

`/ready` относится к общей HTTP/runtime инфраструктуре и не требует здоровья всех провайдеров.

`/health` показывает:

```text
platform_status
products = profile,aero,windgram,cloudgram,map,meteogram,route
features = saved_recipes,settings,schedules
shared resource limits
scheduler.last_error
```

## Telegram isolation

Ошибка `getMe/initialize/start_polling/Application.start` переводит только Telegram в `degraded`. Частично созданный application очищается, FastAPI/MAX/VK продолжают работу.

## MAX/VK isolation

`MessengerWebhookService.from_env()` создаёт gateway отдельно для каждой locally-ready платформы. Невалидный VK не препятствует MAX gateway и наоборот.

Webhook отключённой/невалидной платформы получает свой 503. Event-task exception не завершает process.

## Config preflight

Диагностика:

```bash
python messenger_config_check.py
```

Строго выбранный provider:

```bash
python messenger_config_check.py --strict-telegram
python messenger_config_check.py --strict-max
python messenger_config_check.py --strict-vk
```

Обычный deploy не должен падать из-за optional degraded provider.

## Provider registration

```bash
python register_messenger_webhooks.py
```

MAX и VK регистрируются независимо. Строгая проверка:

```bash
python register_messenger_webhooks.py --max --status
python register_messenger_webhooks.py --vk --status
```

`setup_messenger_bots.sh --max` не валидирует VK; `--vk` не валидирует MAX.

## Common services

```text
/profile     messenger/profile_service.py
/aero        messenger/aero_service.py
/windgram    messenger/windgram_service.py
/cloudgram   messenger/cloudgram_service.py
/map         messenger/map_service.py
/meteogram   messenger/meteogram_service.py
/route       messenger/route_service.py
```

Все они возвращают `CommonProductResult`. Platform gateway только отображает summary/attachments.

`run/cycle` не сохраняются в recipes/schedules. GFS run заново выбирается по максимальному требуемому lead.

## Settings

MAX/VK common state:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Ключ `platform + user_id`. Здесь находятся locations, recipes и common schedules.

`SettingsRecipeStore` зеркалит successful point-products в active/recent location. Route endpoints сохраняются только history и не активируются.

## Scheduler

`MessengerScheduler` — одна asyncio-задача этого процесса. Он использует immutable `ProductSnapshot` и `messenger/product_executor.py`, то есть не импортирует Telegram product runners.

```text
due schedule
→ platform gateway
→ common product service
→ CommonProductResult
→ gateway media
```

Ошибка gateway относится только к соответствующему schedule/platform.

Telegram сохраняет native scheduler UI/JSON storage для backward compatibility, но все product runners, включая route adapter, идут через common services.

## Shared RuntimeResources

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_SCHEDULED=1
```

Один pool используется Telegram/MAX/VK/web/schedules. Fault isolation не означает отдельную квоту тяжёлых расчётов на каждую платформу.

## Production deploy

Fresh install/deploy записывает systemd:

```text
ExecStart=/opt/gfs_profile/.venv/bin/python /opt/gfs_profile/messenger_launcher.py
```

Порядок:

```text
unit/runtime tests
→ messenger config preflight
→ geocoder preflight
→ restart
→ /ready
→ Telegram command registration
→ MAX/VK webhook registration best-effort
```

State/cache/env сохраняются. Подробно: [`../DEPLOY.md`](../DEPLOY.md).
