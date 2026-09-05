# MAX Bot — регистрация, настройка и эксплуатация

Статус на 2026-09-05: MAX transport/webhook и общие `/profile`, `/aero`, `/windgram`, `/cloudgram` работают через тот же messenger-neutral service, что Telegram и VK. Отдельной метеорологической логики MAX нет.

Полная пошаговая регистрация с Nginx, `.env`, диагностикой и типовыми ошибками: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md).

## Что нужно создать в MAX

По актуальной схеме MAX бот создаётся на платформе MAX для партнёров после создания/верификации профиля. Бот проходит модерацию. После успешной модерации token берётся:

```text
Чат-боты
→ выбрать бота
→ Расширенные настройки
→ Настроить
→ Токен
```

Официальные разделы, которые нужно сверять перед изменением transport:

```text
https://dev.max.ru/docs/chatbots/bots-create/create
https://dev.max.ru/docs/chatbots/bots-create/manage
https://dev.max.ru/docs-api
https://dev.max.ru/docs-api/methods/POST/subscriptions
https://dev.max.ru/docs-api/changelog-api
```

Token — секрет. В репозиторий его не коммитить.

## Что вставить в GFS Profile

После базовой установки:

```bash
bash install_telegram_bot.sh
```

запустите мастер:

```bash
sudo bash setup_messenger_bots.sh --max
```

Он запросит только:

```env
MAX_BOT_TOKEN=<token из MAX>
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
```

`MAX_WEBHOOK_SECRET` можно не задавать: мастер создаёт случайный secret сам и хранит его только в `/opt/gfs_profile/.env`.

Неинтерактивно:

```bash
sudo env \
  MAX_BOT_TOKEN='<MAX_TOKEN>' \
  MAX_WEBHOOK_URL='https://bot.example.ru/webhooks/max' \
  bash setup_messenger_bots.sh --max --yes
```

## Production transport

```text
MAX Update
→ POST /webhooks/max
→ MAX adapter
→ NormalizedEvent
→ common product service
→ CommonProductResult
→ MAX gateway
```

API endpoint:

```text
https://platform-api2.max.ru
```

Token передаётся только через `Authorization`. Production использует Webhook `POST /subscriptions`. Проект регистрирует события:

```text
bot_started
message_created
message_callback
```

При регистрации передаётся `secret`; входящий endpoint проверяет:

```text
X-Max-Bot-Api-Secret
```

Webhook быстро отвечает HTTP 200, а тяжёлый GFS-расчёт выполняется фоновой asyncio-task того же процесса. Одновременно отдельный Long Polling `/updates` для production не запускать.

Публичный URL должен быть HTTPS с доверенным сертификатом и проксироваться, например:

```text
https://bot.example.ru/webhooks/max
        ↓
127.0.0.1:8081/webhooks/max
```

## Автоматическая регистрация

После `/ready` штатный deploy вызывает:

```bash
python register_messenger_webhooks.py
```

Скрипт:

1. проверяет публичный endpoint;
2. читает существующие subscriptions;
3. создаёт или обновляет subscription с нужным URL/secret/events.

Проверка фактического состояния:

```bash
cd /opt/gfs_profile
set -a; source .env; set +a
.venv/bin/python register_messenger_webhooks.py --max --status
```

или:

```bash
sudo bash setup_messenger_bots.sh --status
```

Ожидается `OK MAX: подписка активна`.

## Общие продукты

### `/profile`

Город/координаты, `Москва +24`, неоднозначность, native location, быстрые сроки, пагинация до `+384`, один status message, PNG/CSV и saved recipes.

### `/aero`

Один общий Skew-T log-P + годограф. Фактический GFS run/cycle, valid UTC, requested/grid point. Icing/CAT — только модельные прокси.

### `/windgram`

Default:

```text
ветер
+0…+120 ч
шаг 6 ч
до 500 гПа
```

Доступны wind/temp/RH, `+120/+240/+384`, шаг `3/6/12`.

### `/cloudgram`

Default:

```text
режим: Подробно
+0…+72 ч
шаг 3 ч
```

Доступны `Подробно/Кратко`, горизонты `+24/+48/+72/+120`, шаг `3/6`.

Примеры:

```text
/cloudgram Москва to=72 step=3 mode=pro
/cloudgram 55.75 37.62 to=120 step=6 mode=simple
```

Common service выбирает цикл, содержащий максимальный требуемый lead. Сводка показывает actual run/cycle UTC, valid range, requested point, GFS grid, максимальный модельный hazard и missing fields. Гроза/опасность маркируются как модельная диагностика, не наблюдение.

## Saved recipes

SQLite:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Для четырёх общих продуктов сохраняются point + пользовательские параметры, но не `run/cycle`. Repeat выбирает свежий опубликованный цикл. Cloudgram recipe содержит:

```text
from / to / step / mode / point
```

## Общие ресурсы

MAX использует один process-wide pool с Telegram/VK/web:

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Отдельной квоты MAX поверх этих лимитов нет.

## Media и retry

PNG загружается через актуальный MAX upload flow и отправляется как attachment. Client поддерживает retry/backoff для `429`, `5xx` и network errors. Callback подтверждается до тяжёлой обработки.

## Проверка после настройки

```bash
curl -fsS http://127.0.0.1:8081/ready
curl -fsS http://127.0.0.1:8081/health
sudo bash setup_messenger_bots.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

В MAX вручную проверить:

```text
/start
/profile Москва +24
/aero Москва +24
/windgram Москва to=120 step=6 param=wind
/cloudgram Москва to=72 step=3 mode=pro
geo/location
callback
pin/repeat
/status
/cancel
```

## Следующий этап паритета

```text
/map
→ /meteogram
→ /route
→ /settings
→ /schedule
```

Каждый следующий продукт должен использовать common service/result/progress, `RuntimeResources` и `UserRecipeStore`. MAX-копии GFS/geocoder/product logic запрещены.
