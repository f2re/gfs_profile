# MAX Bot — регистрация, настройка и эксплуатация

MAX использует тот же messenger-neutral product layer, что Telegram и VK. Семь исходных продуктов, saved recipes, `/settings` и `/schedule` сохраняются; WeatherNext 3 добавлен отдельным common разделом `/wn3` без копии BigQuery/метеорологической логики.

Полная регистрация: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md). WeatherNext 3: [`docs/WEATHERNEXT3.md`](docs/WEATHERNEXT3.md).

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

Token используется только через `Authorization`. Production использует Webhook subscription для `bot_started`, `message_created`, `message_callback`. Endpoint проверяет `X-Max-Bot-Api-Secret`, быстро отвечает 200 и передаёт тяжёлую работу в asyncio-task текущего процесса. Отдельный production Long Polling для того же бота не запускать.

## 4. Продукты

Common GFS services:

```text
/profile
/aero
/windgram
/cloudgram
/map
/meteogram
/route
```

WeatherNext 3:

```text
/wn3
```

Поддерживаются город/координаты, неоднозначный город, native location, callbacks и одно редактируемое progress message.

### `/wn3`

Раздел MAX использует тот же `messenger/weathernext3_service.py`, что Telegram/VK:

```text
🌡 point forecast
📊 WN3 meteogram
☁ total cloud
☁ low/mid/high cloud layers
🌧 native precipitation
🛰 IMERG precipitation
🧪 experimental precipitation
🌦 cloud + native precip
▶ MP4/GIF animation
```

При входе раздел показывает point forecast +24 ч. Default карт:

```text
+1…+48 ч
step 3 ч
radius 150 км
combo
```

WN3 BigQuery surface grid — 0.1°. T/Td в point/meteogram используют station head 0.05° при наличии. Ensemble statistics: mean/p10/p25/p50/p75/p90 по 64 членам. Фактический init определяется по реально опубликованной таблице и выводится как `Run ...Z`.

Вертикальные WN3 fields в BigQuery отсутствуют; `/profile` и `/aero` не подменяются и остаются GFS до отдельного GCS provider.

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

Доступны GFS, ECMWF IFS/AIFS, ICON, GEM, GEFS/ECMWF ENS/AIFS ENS/ICON-EPS/GEPS и WeatherNext 3 statistics. Форматы PNG/DOCX/PDF.

### `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

PNG и CSV строятся тем же common route service. Run выбирается по максимальному ETA lead.

## 5. WeatherNext 3 config

```env
WEATHERNEXT3_BIGQUERY_PROJECT=<project-with-linked-dataset>
WEATHERNEXT3_BIGQUERY_DATASET=<linked-dataset>
WEATHERNEXT3_BIGQUERY_BILLING_PROJECT=
WEATHERNEXT3_BIGQUERY_LOCATION=
WEATHERNEXT3_BQ_MAX_BYTES_BILLED=0
WEATHERNEXT3_CACHE_TTL=1800
MAX_CONCURRENT_WEATHERNEXT3=2
GOOGLE_APPLICATION_CREDENTIALS=/path/outside/repo/credentials.json
```

BigQuery SDK использует Google Application Default Credentials. Credential JSON не коммитить. Cache живёт в `.cache_gfs/weathernext3` и сохраняется deploy-скриптом вместе с остальным GFS cache.

## 6. Настройки и recipes

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Ключ: `max + user_id`.

`/settings` позволяет выбрать active point, посмотреть последние точки, запускать/закреплять/удалять recipes и очищать персональные настройки. Route endpoints сохраняются в history, но не заменяют active point. `run/cycle` не сохраняются в recipes.

WeatherNext 3 использует общую active point, но в этой версии отдельные WN3 recipes/schedules не записывает.

## 7. Расписания

`/schedule` поддерживает семь исходных common продуктов. Schedule snapshot не содержит `run/cycle`; каждый automatic run получает актуальные данные. WeatherNext 3 пока интерактивный раздел.

Подробно: [`docs/MESSENGER_SCHEDULES.md`](docs/MESSENGER_SCHEDULES.md).

## 8. Fault isolation

```env
MAX_ENABLED=auto
```

Можно временно отключить только MAX:

```env
MAX_ENABLED=0
```

Telegram/VK/web продолжат работать. `/health` показывает состояние каждой платформы независимо.

## 9. Shared resources

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_WEATHERNEXT3=2
MAX_CONCURRENT_SCHEDULED=1
```

Это суммарные process-wide лимиты для всех платформ, а не квота MAX.

## 10. Проверка

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
/wn3 Москва +24
/wn3 Москва kind=clouds to=24 step=3 radius=150
/wn3 Москва kind=precip_native to=24 step=3 radius=150
/route Москва -> Санкт-Петербург
/settings
/schedule
/status
/cancel
```

Все GFS-результаты должны показывать фактический run/cycle и маркировку модели. WN3 должен показывать фактический init/valid UTC и маркировку «модельный прогноз, не наблюдение/радар/спутниковый снимок».
