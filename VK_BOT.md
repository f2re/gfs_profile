# VK Bot — текущее состояние и эксплуатация

Статус на 2026-09-04: Callback API transport, общие `/profile` и `/aero` vertical slices и сохранённые сценарии этих продуктов реализованы в рабочей ветке `telegram-bot`. Остальные продукты переносятся в общий messenger service без копирования метеорологической логики.

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

`recipe_id` позволяет повторять и закреплять сценарий после restart процесса без process-local wizard state.

## Реализованный `/profile`

Через общий service работают город/координаты, `Москва +24`, неоднозначный город, native geo/location, быстрые сроки, все сроки до `+384`, редактируемый progress, общий GFS run selection и одинаковая итоговая сводка PNG/CSV.

## Реализованный `/aero`

VK использует тот же `messenger/aero_service.py`, что Telegram и MAX. Расчётная часть не находится в VK adapter/gateway.

Flow:

```text
/aero Москва +24
→ сразу расчёт

/aero
→ город / координаты / геолокация
→ неоднозначный город при необходимости
→ срок
→ расчёт
```

Доступны быстрые `+0,+3,+6,+12,+24,+48` и пагинация канонических сроков до `+384`.

Результат одинаков с MAX/Telegram по метеорологическому contract:

- фактический GFS run/cycle UTC;
- lead и valid UTC;
- requested point;
- GFS grid point;
- Skew-T log-P;
- годограф;
- PNG;
- GFS явно обозначен как модель;
- icing/CAT обозначены как модельные прокси.

`/aero` всегда строит Skew-T log-P с годографом; отдельного переключателя Stüve/Emagram нет.

Подробно: `docs/MESSENGER_AERO_SERVICE.md` и `docs/AERO_DIAGRAM.md`.

## Сохранённые сценарии

MAX/VK используют общий messenger-neutral store:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Для `/profile` и `/aero` успешный результат сохраняет точку и параметры. `run/cycle` не входит в recipe. `/start` показывает до двух быстрых сценариев; команда продукта без аргументов открывает закреплённый или последний успешный вариант. Repeat выбирает актуальный опубликованный GFS run.

Recipes изолированы по `platform + user_id`, поэтому одинаковый числовой ID в MAX и VK не означает одного пользователя.

## Media

VK gateway отвечает за platform upload flow для изображений/файлов и отправку attachment через `messages.send`. Общий product service возвращает platform-neutral attachments; метеорологический код ничего не знает про VK attachment ids.

## Следующий этап паритета

Следующий vertical slice — `/windgram`, затем:

```text
/cloudgram
/meteogram
/map
/route
/settings
/schedule
```

Каждый vertical slice должен использовать общие contracts, formatter, run selection, progress и `UserRecipeStore`. Локальные VK-копии GFS/geocoder/product logic не допускаются.
