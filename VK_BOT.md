# VK Bot — текущее состояние и эксплуатация

Статус на 2026-09-04: Callback API transport, общий `/profile` vertical slice и сохранённые profile-сценарии реализованы в ветке `telegram-bot`. Остальные продукты переносятся в общий messenger service по тем же контрактам, без копирования метеорологической логики.

## Архитектура

```text
VK Callback event
→ VK adapter
→ NormalizedEvent
→ common messenger router/service
→ CommonProductResult
→ VK gateway/renderer
```

VK-слой отвечает только за transport, native keyboard, media upload/send, callback acknowledgement и platform-specific ограничения.

## Production transport

Используется Community Bot + Callback API. Основные входящие типы:

```text
confirmation
message_new
message_event
```

`confirmation` обслуживается transport layer. `message_new` нормализует команды, обычный текст и geo. `message_event` используется для callback-кнопок.

Webhook проверяет `group_id`, configured callback secret, выполняет dedupe по platform event id и быстро возвращает VK success response. Долгий GFS-расчёт не выполняется внутри HTTP request lifecycle.

## Конфигурация

```env
MESSENGER_RUNTIME_ENABLED=1
VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Версия API задаётся конфигурацией и не размазывается литералом по коду.

## Кнопки и callbacks

Gateway переводит общий `UiKeyboard` в VK keyboard. Для callback используется `messages.sendMessageEventAnswer`; `messages.send` получает уникальный `random_id`, чтобы retry не создавал дублей.

Saved recipe callbacks используют общий versioned codec:

```text
v1|recipe|run|<id>
v1|recipe|toggle|<id>
v1|recipe|change|<id>
```

`recipe_id` позволяет повторять и закреплять сценарий после рестарта процесса без process-local wizard state.

## Реализованный `/profile` flow

Через общий service работают:

- `/start`;
- `/profile`;
- город/координаты;
- `Москва` → выбор срока;
- `Москва +24` → немедленный профиль;
- неоднозначный город → callback-выбор;
- native geo/location;
- быстрые `+0,+3,+6,+12,+24,+48`;
- все сроки до `+384`;
- `/status`, `/cancel`;
- редактируемый progress/status message;
- общий GFS run selection;
- одинаковая итоговая сводка, PNG и CSV.

## Сохранённые profile-сценарии

MAX/VK используют общий messenger-neutral store:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Успешный профиль сохраняет точку и lead. `run/cycle` не входит в recipe. `/start` показывает до двух быстрых profile-сценариев; `/profile` без аргументов открывает закреплённый или последний успешный. Повтор всегда запускает актуальный опубликованный GFS run.

Recipes изолированы по `platform + user_id`, поэтому одинаковый числовой ID в MAX и VK не означает одного пользователя.

Подробно: `docs/MESSENGER_SAVED_RECIPES.md`.

## Media

VK gateway отвечает за platform upload flow для изображений/файлов и отправку attachment через `messages.send`. Общий product service возвращает platform-neutral attachments; метеорологический код ничего не знает про VK attachment ids.

## Следующий этап паритета

Последовательно вынести в общий service:

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

Каждый vertical slice должен использовать общие contracts, formatter, run selection, progress и `UserRecipeStore`. Локальные VK-копии GFS/geocoder/product logic не допускаются.
