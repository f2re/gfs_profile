# Multi-messenger runtime: Telegram + MAX + VK

Статус: реализованы transport/webhook, общие `/profile` и `/aero` vertical slices и общий слой saved recipes для этих продуктов. Остальные продукты последовательно переносятся в messenger-neutral services.

## Один процесс

```text
Telegram polling
MAX POST /webhooks/max
VK  POST /webhooks/vk
web/API
```

Runtime остаётся single-process. Несколько uvicorn workers запрещены из-за текущего GRIB/cache locking. При `MESSENGER_RUNTIME_ENABLED=0` сохраняется Telegram-only polling; при `1` запускается общий ASGI runtime.

## Конфигурация

```env
MESSENGER_RUNTIME_ENABLED=0
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081
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

Публичный HTTPS завершается Nginx/HAProxy; внутренний порт напрямую не публикуется.

## Общие продукты

### `/profile`

MAX/VK через общий router/service поддерживают `/start`, `/profile`, город/координаты, `Москва +24`, неоднозначный город, native location, быстрые сроки, пагинацию до `+384`, `/status`, `/cancel`, редактируемый progress, общий GFS run selection и одинаковый результат PNG/CSV.

Telegram `/profile` использует тот же `messenger/profile_service.py`.

### `/aero`

`/aero` использует `messenger/aero_service.py` во всех трёх мессенджерах. Общий service владеет parser, lead validation, выбором фактического опубликованного GFS run, вызовом существующего `aero_product.py`, progress contract и `CommonProductResult`.

MAX/VK поддерживают:

- `/aero Москва +24` → прямой расчёт;
- `/aero` → город/координаты/native location → срок;
- неоднозначный город → callback-выбор;
- быстрые `+0,+3,+6,+12,+24,+48`;
- пагинацию всех сроков до `+384`;
- одно редактируемое status message;
- Skew-T log-P с годографом;
- одинаковую модельную сводку и PNG.

Результат явно показывает фактический run/cycle, lead, valid UTC, requested point и GFS grid point. Icing/CAT обозначаются как модельные прокси; продукт не называется радиозондом или наблюдением.

Подробно: `docs/MESSENGER_AERO_SERVICE.md` и `docs/AERO_DIAGRAM.md`.

## Saved recipes

`messenger/user_recipes.py` хранит сценарии по `platform + user + product + point + params`. `run/cycle` и process-local state исключаются.

Для `/profile` и `/aero` MAX/VK поддерживают:

- успешный расчёт создаёт/обновляет recipe;
- `/start` показывает до двух быстрых recipes;
- команда продукта без аргументов открывает закреплённый или последний успешный вариант;
- callback использует устойчивый `recipe_id`;
- repeat запускает `run=None` и выбирает новый доступный GFS cycle;
- pin/unpin переживает restart процесса.

Telegram использует тот же логический recipe contract в своём персональном UX.

Подробно: `docs/MESSENGER_SAVED_RECIPES.md`.

## Следующий продуктовый этап

Переносить в common service по одному vertical slice:

```text
/windgram
/cloudgram
/meteogram
/map
/route
/settings
/schedule
```

Следующий приоритет — `/windgram`. Каждый новый продукт сразу использует общий result/progress contract и `UserRecipeStore`; платформенная копия метеорологической логики запрещена.
