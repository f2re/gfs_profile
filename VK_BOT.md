# VK Bot — текущее состояние и эксплуатация

Статус на 2026-09-05: Callback API transport, общие `/profile` и `/aero` vertical slices, saved recipes и штатный production multi-messenger runtime реализованы в рабочей ветке `telegram-bot`. Остальные продукты переносятся в общий messenger service без копирования метеорологической логики.

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

## Production install/deploy

Обычные install/deploy теперь сразу используют `messenger_launcher.py`:

```bash
bash install_telegram_bot.sh
sudo bash deploy_telegram_bot.sh --yes
```

При `MESSENGER_RUNTIME_ENABLED=1` один systemd-процесс содержит Telegram polling, MAX/VK webhook и web/API. Старый Telegram-only unit автоматически мигрируется при deploy.

VK регистрация выполняется только после локального `GET /ready`:

```text
offline env preflight
→ restart
→ /ready = 200
→ groups.getCallbackConfirmationCode
→ add/update callback server
→ groups.setCallbackSettings
```

Публичный HTTPS URL обычно проксируется Nginx/HAProxy на:

```text
127.0.0.1:8081/webhooks/vk
```

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

Версия API задаётся конфигурацией и не размазывается литералом по коду. Пустой `VK_BOT_TOKEN` отключает VK gateway без отключения Telegram/runtime.

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

## Общие лимиты ресурсов

VK использует тот же process-wide pool, что Telegram/MAX/web:

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Платформа не создаёт свой отдельный semaphore и не может обойти общий серверный лимит.

## Media

VK gateway отвечает за platform upload flow для изображений/файлов и отправку attachment через `messages.send`. Общий product service возвращает platform-neutral attachments; метеорологический код ничего не знает про VK attachment ids.

## Проверка

```bash
curl -fsS http://127.0.0.1:8081/ready
sudo bash deploy_telegram_bot.sh --status
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

`register_messenger_webhooks.py` проверяет confirmation code и актуализирует callback server/settings после каждого штатного deploy.

## Следующий этап паритета

Следующий vertical slice — `/windgram`, затем:

```text
/cloudgram
/map
/meteogram
/route
/settings
/schedule
```

Каждый vertical slice должен использовать общие contracts, formatter, run selection, progress, `RuntimeResources` и `UserRecipeStore`. Локальные VK-копии GFS/geocoder/product logic не допускаются.
