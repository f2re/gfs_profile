# MAX Bot — регистрация, настройка и эксплуатация

MAX использует тот же messenger-neutral product layer, что Telegram и VK. Все семь основных продуктов, saved recipes, `/settings` и `/schedule` доступны без копии метеорологической логики.

Полная регистрация: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md). Итоговый паритет: [`docs/MESSENGER_PARITY.md`](docs/MESSENGER_PARITY.md).

## 1. Что создать в MAX

Бот создаётся на платформе MAX для партнёров и проходит требуемую платформой модерацию. После готовности бота token берётся в интерфейсе управления чат-ботом:

```text
Чат-боты → бот → Расширенные настройки → Настроить → Токен
```

Перед изменением transport сверять официальные материалы:

```text
https://dev.max.ru/docs/chatbots/bots-create/create
https://dev.max.ru/docs/chatbots/bots-create/manage
https://dev.max.ru/docs-api
https://dev.max.ru/docs-api/methods/POST/subscriptions
https://dev.max.ru/docs-api/changelog-api
```

Token — секрет, в Git не коммитить.

## 2. Что вставить в проект

После базовой установки:

```bash
sudo bash setup_messenger_bots.sh --max
```

Вручную нужны только:

```env
MAX_BOT_TOKEN=<token>
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
```

`MAX_WEBHOOK_SECRET` мастер генерирует и сохраняет в `/opt/gfs_profile/.env`.

Неинтерактивно:

```bash
sudo env \
  MAX_BOT_TOKEN='<MAX_TOKEN>' \
  MAX_WEBHOOK_URL='https://bot.example.ru/webhooks/max' \
  bash setup_messenger_bots.sh --max --yes
```

MAX production Webhook должен иметь публичный доверенный HTTPS endpoint на 443. Внутри сервера runtime по умолчанию слушает `127.0.0.1:8081`, поэтому нужен reverse proxy.

## 3. Transport

```text
MAX Update
→ POST /webhooks/max
→ secret validation
→ NormalizedEvent
→ common router/service
→ CommonProductResult
→ MaxGateway
```

API:

```text
https://platform-api2.max.ru
```

Token используется только через `Authorization`. Production использует Webhook subscription для:

```text
bot_started
message_created
message_callback
```

Endpoint проверяет `X-Max-Bot-Api-Secret`, быстро отвечает 200 и передаёт тяжёлую работу в asyncio-task текущего процесса. Отдельный production Long Polling для того же бота не запускать.

Регистрация после deploy:

```bash
.venv/bin/python register_messenger_webhooks.py --max
.venv/bin/python register_messenger_webhooks.py --max --status
```

Ожидается `OK MAX: подписка активна`.

## 4. Продукты

Работают одинаковые common services:

```text
/profile
/aero
/windgram
/cloudgram
/map
/meteogram
/route
```

Поддерживаются город/координаты, неоднозначный город, native location для point-products, callbacks, progress, saved recipes, repeat/pin.

### `/map`

Default нового пользователя:

```text
Анимация +0…+48 ч
step 3 ч
17 кадров
radius 100 км
places
```

MP4 отправляется как native `video` attachment.

### `/meteogram`

Доступны GFS, ECMWF IFS/AIFS, ICON, GEM и ансамбли GEFS/ECMWF ENS/AIFS ENS/ICON-EPS/GEPS. Форматы PNG/DOCX/PDF.

### `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

PNG и CSV строятся тем же common route service. Run выбирается по максимальному ETA lead.

## 5. Настройки и recipes

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Ключ:

```text
max + user_id
```

`/settings` позволяет выбрать active point, посмотреть последние точки, запускать/закреплять/удалять recipes и очищать персональные настройки.

Route endpoints сохраняются в history, но не заменяют active point.

`run/cycle` не сохраняются в recipes.

## 6. Расписания

`/schedule` поддерживает все семь продуктов.

```text
saved recipe
→ частота 1/2/3/7 или 1–30 дней
→ local HH:MM
→ IANA timezone
→ подтверждение
```

Schedule snapshot не содержит `run/cycle`; каждый automatic run получает актуальные данные. Ограничение — два schedule на `max + user_id`.

Подробно: [`docs/MESSENGER_SCHEDULES.md`](docs/MESSENGER_SCHEDULES.md).

## 7. Fault isolation

```env
MAX_ENABLED=auto
```

Можно временно отключить только MAX:

```env
MAX_ENABLED=0
```

Telegram/VK/web продолжат работать. И наоборот, `VK=degraded` не мешает MAX. `/health` показывает состояние каждой платформы независимо.

Ошибка MAX schedule не останавливает scheduler и не влияет на VK/Telegram schedules.

## 8. Shared resources

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_SCHEDULED=1
```

Это суммарные process-wide лимиты для всех платформ, а не квота MAX.

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

Все GFS-результаты должны показывать фактический run/cycle и маркировку «модель, не наблюдение/радиозонд».