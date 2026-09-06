# Общие расписания продукции

## Цель

Расписание — это не сохранённый Telegram wizard и не копия product logic. Оно хранит immutable snapshot уже успешного common product и при каждом запуске заново вызывает тот же messenger-neutral service, который используется интерактивно.

Для MAX/VK состояние хранится в:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Telegram сохраняет совместимый native scheduler UI/JSON storage, но все семь его product runners используют те же common services. Дополнительный adapter `telegram_schedule_route_compat.py` добавляет недостающий маршрут без дублирования метеорологии.

## Поддерживаемые продукты

Одинаковый набор на Telegram, MAX и VK:

```text
profile
aero
windgram
cloudgram
map
meteogram
route
```

## Immutable ProductSnapshot

`messenger/product_executor.py` принимает:

```text
product
point
params
```

Из snapshot удаляются transient значения:

```text
run
cycle
run_date
run_cycle
message/status ids
callback ids
geocoder candidates
wizard state
```

Поэтому автоматическая отправка всегда выбирает актуальные модельные данные. Для GFS service снова проверяет публикацию максимального реально нужного lead; для Open-Meteo meteogram не придумывается неизвестный model cycle.

Route snapshot содержит `origin/destination` в params. Точка расписания — origin, поэтому локальный часовой пояс определяется по месту вылета. Route schedule не меняет global active point.

## UX MAX/VK

```text
/schedule
→ Новое расписание
→ выбрать сохранённый успешный recipe
→ частота 1/2/3/7 дней или 1–30
→ местное время
→ автоматическое определение IANA timezone
→ подтверждение
```

Для готового schedule:

```text
Прислать сейчас
Удалить
Вернуться к списку
```

`Прислать сейчас` не изменяет `next_run_utc`.

Ограничение — два активных расписания на `platform + user_id`. Одинаковый числовой user id в MAX и VK имеет независимые квоты и данные.

## Scheduler loop

`messenger/scheduler.py` работает одной `asyncio`-задачей внутри штатного single-process runtime.

```text
claim due schedules
→ заранее сдвинуть next_run
→ найти gateway платформы
→ ProductSnapshot → common service
→ CommonProductResult
→ platform gateway
→ mark success/error
```

Next run сдвигается **до** выполнения. Это предотвращает повторную доставку после падения процесса между генерацией и отправкой.

После длительного простоя слишком старые сроки пропускаются и переносятся в будущее.

## Fault isolation

Gateway определяется отдельно для каждого due item.

Пример:

```text
Telegram: ready
MAX: ready
VK: degraded/off
```

MAX schedules продолжают выполняться. Due VK schedule получает локальный `error: platform vk unavailable` и новый будущий срок. Ошибка VK не останавливает scheduler loop, MAX, Telegram или web/API.

И наоборот, ошибка MAX не влияет на VK/Telegram.

## Shared resources

Schedule executor использует тот же process-wide `RuntimeResources`:

```text
meteogram → meteogram_semaphore
остальные common products → gfs_semaphore
```

Дополнительно `MAX_CONCURRENT_SCHEDULED` ограничивает одновременное число автоматических задач. Redis/Celery не требуются.

## Время и DST

Хранятся:

```text
IANA timezone
local HH:MM
every_days
next_run_utc
```

Следующий срок считается по локальному календарю, поэтому `06:00 Europe/London` остаётся 06:00 после перехода GMT/BST, хотя UTC-время меняется.

Часовой пояс определяется по координатам через Open-Meteo `timezone=auto`. Ошибка определения не создаёт частично заполненное расписание.

## Переменные окружения

```env
MESSENGER_SCHEDULE_POLL_SECONDS=30
MESSENGER_SCHEDULE_MAX_LATE_MINUTES=180
MESSENGER_SCHEDULE_TIMEZONE_TIMEOUT=10
MAX_CONCURRENT_SCHEDULED=1
```

Telegram compatibility:

```env
TELEGRAM_SCHEDULE_FILE=.cache_gfs/telegram_schedules.json
TELEGRAM_SCHEDULE_POLL_SECONDS=30
TELEGRAM_SCHEDULE_MAX_LATE_MINUTES=180
TELEGRAM_SCHEDULE_TIMEZONE_TIMEOUT=10
```

## Удаление персональных данных

`/settings → Удалить мои настройки` удаляет locations/recipes, но не schedules. Расписание может продолжать работать по собственному immutable snapshot до явного удаления через `/schedule`.

Это намеренно: скрытое удаление автоматической рассылки вместе с preference reset запрещено.
