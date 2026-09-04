# Multi-messenger runtime: Telegram + MAX + VK

Статус: реализован transport/webhook, общий `/profile` vertical slice и общий слой saved recipes для профиля. Остальные продукты последовательно переносятся в messenger-neutral service.

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

## Реализованный общий `/profile`

MAX/VK через общий router/service поддерживают `/start`, `/profile`, город/координаты, `Москва +24`, неоднозначный город, native location, быстрые сроки, пагинацию до `+384`, `/status`, `/cancel`, редактируемый progress, общий GFS run selection и одинаковый результат PNG/CSV.

## Saved recipes

`messenger/user_recipes.py` хранит сценарии по `platform + user + product + point + params`. `run/cycle` и process-local state исключаются.

`messenger/personal_router.py` подключает recipe UX к общему profile vertical slice MAX/VK:

- успешный профиль создаёт/обновляет recipe;
- `/start` показывает до двух быстрых profile recipes;
- `/profile` без аргументов открывает закреплённый или последний успешный;
- callback использует устойчивый `recipe_id`;
- повтор запускает `run=None` и выбирает новый доступный GFS cycle;
- pin/unpin переживает restart процесса.

Telegram использует тот же recipe contract, но хранит свои сценарии в `TELEGRAM_PREFERENCES_DB` и поддерживает recipes для всех существующих продуктов.

Подробно: `docs/MESSENGER_SAVED_RECIPES.md`.

## Следующий продуктовый этап

Переносить в common service по одному vertical slice:

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

Каждый новый продукт сразу использует общий result/progress contract и `UserRecipeStore`; платформенная копия метеорологической логики запрещена.
