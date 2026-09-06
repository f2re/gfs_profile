# VK Bot — регистрация, настройка и эксплуатация

VK использует Community Bot + Callback API как platform adapter поверх общего messenger-neutral слоя. Все семь основных продуктов, saved recipes, `/settings` и `/schedule` доступны без копирования GFS/geocoder/product logic.

Полная регистрация: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md). Итоговый паритет: [`docs/MESSENGER_PARITY.md`](docs/MESSENGER_PARITY.md).

## 1. Что создать в VK

1. Создайте сообщество VK или используйте существующее с правами администратора.
2. Включите сообщения сообщества.
3. Откройте `Управление → Работа с API`.
4. Создайте community access token с нужными правами сообщений/media/Callback API.
5. Скопируйте token.
6. Запишите положительный числовой ID сообщества без `-`.

Проект использует community token, а не user token.

Основные методы API:

```text
messages.send
messages.edit
messages.sendMessageEventAnswer
photos.getMessagesUploadServer
photos.saveMessagesPhoto
docs.getMessagesUploadServer
docs.save
video.save
groups.getCallbackConfirmationCode
groups.getCallbackServers
groups.addCallbackServer
groups.getCallbackSettings
groups.setCallbackSettings
```

## 2. Что вставить в проект

```bash
sudo bash setup_messenger_bots.sh --vk
```

Нужны:

```env
VK_BOT_TOKEN=<community access token>
VK_GROUP_ID=123456789
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
```

Можно оставить пустыми:

```env
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
```

Мастер создаёт secret, получает confirmation code через VK API, сохраняет `.env` и после `/ready` создаёт/обновляет Callback API server.

Неинтерактивно:

```bash
sudo env \
  VK_BOT_TOKEN='<VK_COMMUNITY_TOKEN>' \
  VK_GROUP_ID='123456789' \
  VK_CALLBACK_URL='https://bot.example.ru/webhooks/vk' \
  bash setup_messenger_bots.sh --vk --yes
```

## 3. Callback transport

```text
VK event
→ POST /webhooks/vk
→ group/secret validation
→ NormalizedEvent
→ common router/service
→ CommonProductResult
→ VkGateway
```

Поддерживаются:

```text
confirmation
message_new
message_event
```

Регистрационный скрипт сверяет confirmation code, проверяет публичный endpoint, создаёт/находит Callback API server и включает `message_new/message_event`.

Проверка:

```bash
.venv/bin/python register_messenger_webhooks.py --vk --status
```

Ожидается `OK VK: callback server активен`.

## 4. Продукты

```text
/profile
/aero
/windgram
/cloudgram
/map
/meteogram
/route
```

Поддерживаются город/координаты, неоднозначный город, native location для point-products, callback flow, progress и saved recipes.

### `/map`

Default: MP4 animation `+0…+48 ч`, step 3, radius 100 км, places. VK gateway сначала использует native video upload (`video.save`). Если конкретный token/API не позволяет video upload, применяется document fallback; результат не теряется.

### `/meteogram`

GFS/ECMWF/ICON/GEM и ансамбли, PNG/DOCX/PDF. Неизвестный upstream model cycle не выдумывается.

### `/route`

Одинаковый common PNG+CSV, run выбирается по max ETA lead.

## 5. Settings / recipes

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

State изолирован по:

```text
vk + user_id
```

`/settings` управляет active/recent points и recipes. Route endpoints не заменяют active point. `run/cycle` в recipes не сохраняются.

## 6. Расписания

`/schedule` поддерживает все семь продуктов:

```text
saved recipe → частота → local time → timezone → сохранить
```

Каждый automatic run использует свежие common services. До двух schedule на `vk + user_id`.

Если VK gateway временно `degraded/off`, due VK schedule получает локальную ошибку и переносится на следующий срок. Scheduler/MAX/Telegram продолжают работать.

## 7. Fault isolation

```env
VK_ENABLED=auto
```

Карантин только VK:

```env
VK_ENABLED=0
```

Telegram/MAX/web остаются доступны. `/health` показывает состояние платформ отдельно.

## 8. Shared limits

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_SCHEDULED=1
```

Они суммарные для всего процесса.

## 9. Проверка

```bash
curl -fsS http://127.0.0.1:8081/ready
curl -fsS http://127.0.0.1:8081/health
sudo bash setup_messenger_bots.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

Ручной smoke:

```text
/start
/profile Москва +24
/aero Москва +24
/windgram Москва
/cloudgram Москва
/map Москва
/meteogram Москва source=gfs days=5
/route Москва -> Санкт-Петербург
/settings
/schedule
/status
/cancel
```

Все GFS-продукты должны быть помечены как модель, не наблюдение/радиозонд.