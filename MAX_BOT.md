# MAX Bot — требования и план интеграции

Статус: спецификация перед реализацией. Проверено по официальной документации MAX 2026-09-04.

## Цель

MAX должен быть полноценным транспортом того же GFS-сервиса, а не отдельной реализацией метеорологического бота.

Пользователь MAX должен получать тот же прогноз, тот же фактический GFS run, те же единицы, диагностику и файлы, что пользователь Telegram при одинаковом запросе.

Целевая цепочка:

`MAX Update → MAX adapter → normalized event → common messenger service → common product result → MAX renderer`.

## API и режим доставки

Использовать:

```text
https://platform-api2.max.ru
```

Токен передавать только заголовком `Authorization`.

Production — Webhook. Long Polling `/updates` допускается только для разработки/диагностики. Одновременно Webhook и Long Polling не использовать.

Webhook регистрируется через `POST /subscriptions` и должен быть HTTPS endpoint с доверенным TLS. При регистрации задавать `secret`; каждый входящий запрос проверять по `X-Max-Bot-Api-Secret`.

Минимальные типы обновлений:

```text
bot_started
message_created
message_callback
```

При необходимости позже добавить lifecycle events (`bot_stopped`, `bot_removed`) для очистки/аналитики.

Официальные источники:

- https://dev.max.ru/docs-api
- https://dev.max.ru/docs-api/methods/POST/subscriptions
- https://dev.max.ru/docs-api/objects/Update
- https://dev.max.ru/docs-api/changelog-api

Перед каждой существенной правкой MAX повторно сверять reference и changelog.

## Входящие события

### bot_started

Нормализовать в `START` и запускать общий `/start` flow.

### message_created

Разобрать:

- текст/команду;
- координаты/location;
- идентификаторы user/chat/message;
- платформенный event id для dedupe.

Обычный текст `Москва` должен пойти в тот же geocoder/use-case, что Telegram.

`Москва +24` — прямой расчёт без дополнительного выбора срока.

### message_callback

Callback быстро подтвердить через MAX API, затем передать payload в общий action router.

Payload должен быть versioned и не зависеть только от RAM-state.

## Кнопки

MAX renderer должен поддерживать общие действия:

- callback;
- request location;
- text/quick reply;
- link, если понадобится.

Для геолокации использовать native `request_geo_location`.

Кнопки по смыслу и названиям должны совпадать с Telegram/VK, но генерироваться native MAX markup.

## Status message

Долгая операция создаёт одно status message и далее редактирует его через Messages API.

Пример:

```text
⏳ Профиль GFS
📍 Краснодар
🕒 +24 ч
3/5 Загружаю модельные данные…
```

Webhook не ждёт расчёт. После валидации событие передаётся в async task внутри приложения, HTTP handler сразу завершает запрос.

Не обновлять status чаще, чем это оправдано изменением этапа. Обрабатывать `429`, `5xx` и сетевые ошибки с bounded exponential backoff + jitter.

## Media

Актуальный flow MAX:

`POST /uploads → upload URL/token → загрузка файла → POST /messages`.

Для изображений использовать `type=image`; `type=photo` больше не поддерживается.

Обязательные форматы проекта:

- PNG — image attachment;
- CSV/DOCX/PDF — file attachment;
- MP4/GIF карты — подходящий media/file attachment согласно актуальному API.

Не хранить MAX upload token как долгоживущий идентификатор результата.

## Базовый UX parity

Первая production-версия MAX должна поддерживать:

```text
/start
/help
/status
/cancel
/profile
/aero
/windgram
/cloudgram
/meteogram
/map
```

Flow:

```text
город → неоднозначность при необходимости → срок → расчёт
город +24 → сразу расчёт
геолокация → срок → расчёт
```

Быстрые сроки:

```text
+0 +3 +6 +12 +24 +48
```

Все сроки до +384 доступны через пагинацию.

Результат профиля: текстовая метеосводка + PNG + CSV. Остальные продукты — тот же набор файлов/форматов, который определён common product service.

## Метеорологический контракт

MAX не имеет права самостоятельно менять значения или подписи.

В общем результате показывать:

- фактический GFS run/cycle UTC;
- lead и valid UTC;
- requested point;
- GFS grid point;
- p/Z, T/Td, RH;
- направление ветра «откуда» и скорость;
- изотермы 0/-10/-20 °C;
- max wind/число уровней, где применимо;
- `GFS 0.25° grid • модельный прогноз, не радиозонд и не наблюдение`.

## HTTP client

Предпочтителен небольшой async client поверх поддерживаемого HTTP стека проекта. Не вводить тяжёлый сторонний SDK без проверки, что он соответствует текущему MAX API.

Client отвечает только за:

- auth headers;
- serialization;
- send/edit/answer callback;
- media upload;
- timeout;
- retry/backoff;
- классификацию platform errors.

Метеорологические решения в client запрещены.

## Конфигурация

Планируемые env:

```env
MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=https://example.org/webhooks/max
MAX_WEBHOOK_SECRET=
MAX_API_BASE=https://platform-api2.max.ru
MAX_HTTP_TIMEOUT=20
```

Секреты не коммитить.

## Deploy

Первый этап — один Python process вместе с Telegram polling и VK webhook. Это сохраняет текущую process-local защиту GFS cache.

Снаружи HTTPS reverse proxy направляет URL MAX webhook на локальный ASGI endpoint.

Разносить MAX в отдельный process можно только после межпроцессной блокировки GRIB cache key.

## Тесты

Обязательно:

1. secret valid/invalid;
2. `bot_started`;
3. текстовый `message_created`;
4. location;
5. callback + callback answer;
6. ambiguous city;
7. lead pagination;
8. status edit;
9. image upload;
10. file upload;
11. 429 retry;
12. 5xx/network retry;
13. duplicate webhook event;
14. общий profile contract совпадает с Telegram/VK.

Live MAX test с реальным токеном не должен быть обязательным для CI; предусмотреть отдельный ручной smoke.

## Definition of Done

MAX gateway готов, когда общий профиль по одинаковым координатам/run/lead даёт тот же common result, что Telegram/VK, native кнопки и location работают, status редактируется одним сообщением, PNG/CSV доходят пользователю, callback подтверждается, webhook защищён secret и весь platform-specific код остаётся в adapter/client/renderer.
