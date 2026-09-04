# VK Bot — требования и план интеграции

Статус: спецификация перед реализацией. Базовый контракт сверяется с официальным VK API и `VKCOM/vk-api-schema` перед началом реализации.

## Цель

VK — транспорт общего GFS messenger service. Он не содержит отдельную копию geocoder, run selection, метеорологических расчётов или formatter.

Целевая цепочка:

`VK Callback event → VK adapter → normalized event → common messenger service → common result → VK renderer`.

## Режим интеграции

Целевая production-схема: Community Bot + Callback API.

Минимальные входящие типы:

```text
confirmation
message_new
message_event
```

`confirmation` обслуживается только transport layer и возвращает код подтверждения сообщества.

`message_new` нормализуется в обычное сообщение/команду/location.

`message_event` используется для callback-кнопок.

Перед существенной правкой проверять:

- https://dev.vk.com
- https://github.com/VKCOM/vk-api-schema

Версию VK API не размазывать литералом по коду. Использовать конфигурацию `VK_API_VERSION` с актуальным проверенным default.

## Авторизация и проверка webhook

Хранить в `.env`:

```env
VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=
```

В Callback endpoint:

1. разобрать JSON;
2. проверить `group_id`;
3. проверить callback secret, если он настроен;
4. обработать `confirmation` синхронно;
5. для рабочих событий сделать dedupe по платформенному event id;
6. нормализовать событие;
7. передать обработку async task;
8. немедленно вернуть требуемый VK успешный ответ.

GFS расчёт внутри HTTP request lifecycle запрещён.

## Сообщения

VK client должен покрывать минимум:

```text
messages.send
messages.edit
messages.sendMessageEventAnswer
```

При `messages.send` всегда использовать уникальный `random_id`, чтобы retry не создавал дубли.

Transport client отвечает за timeout, retry/backoff для временных ошибок, сериализацию keyboard/attachments и классификацию API errors.

## Кнопки

Общая модель кнопок одна для Telegram/MAX/VK:

```text
callback
request_location
text
link
```

VK renderer преобразует её в VK keyboard.

Для callback использовать action, приводящий к `message_event`; payload должен быть компактным и versioned.

Для геолокации использовать native location action VK, если он поддерживается текущим API клиента/клавиатуры. При получении координат adapter формирует общую `Location(lat, lon)`.

## Media upload

PNG:

```text
photos.getMessagesUploadServer
→ upload
→ photos.saveMessagesPhoto
→ messages.send attachment
```

CSV/DOCX/PDF и другие файлы:

```text
docs.getMessagesUploadServer
→ upload
→ docs.save
→ messages.send attachment
```

Фактические сигнатуры методов и тип upload server перед реализацией повторно сверить с актуальной версией VK API.

MP4/GIF карты отправлять как поддерживаемое VK media/file attachment без изменения common product result.

## Базовый UX parity

Первая production-версия VK должна поддерживать:

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

Пользовательские сценарии:

```text
Москва
→ поиск точки
→ при неоднозначности native buttons
→ выбор срока
→ расчёт

Москва +24
→ сразу расчёт

location
→ выбор срока
→ расчёт
```

Быстрые сроки:

```text
+0 +3 +6 +12 +24 +48
```

Остальные сроки до +384 — через пагинацию.

## Status message

Как Telegram/MAX, VK редактирует одно статусное сообщение:

```text
⏳ Метеограмма GFS
📍 Москва
2/5 Получаю модельные данные…
```

Renderer хранит только platform handle/status message id, common service не знает о VK id.

При невозможности редактирования из-за ограничений API допускается fallback на одно финальное сообщение, но не spam progress-сообщениями. Такой fallback должен быть явным в adapter policy и покрыт тестом.

## Метеорологический контракт

Результат формируется общим formatter/view-model. VK не меняет:

- actual run/cycle UTC;
- lead/valid UTC;
- requested point и GFS grid;
- p/Z, T/Td, RH;
- wind from + speed;
- уровни 0/-10/-20 °C;
- max wind и число уровней;
- маркировку модели.

Обязательная формулировка по смыслу:

`GFS 0.25° grid • модельный прогноз, не радиозонд и не наблюдение`.

## State

Не использовать отдельную `vk_preferences.sqlite3` со своей схемой.

Целевой ключ preferences:

```text
(platform='vk', user_id=<vk id>)
```

Session-state при необходимости включает `peer_id/chat_id`.

Callback state по возможности stateless. Устойчивые defaults/preferences хранятся в общей SQLite schema.

## Deploy

Первый релиз VK работает в том же Python process, что Telegram polling и MAX Webhook.

ASGI endpoint:

```text
POST /webhooks/vk
```

Публичный HTTPS endpoint выдаёт reverse proxy.

Отдельный VK process допускается позже после внедрения межпроцессной блокировки GRIB cache.

## Тесты

Минимальный набор:

1. confirmation;
2. wrong group id;
3. wrong secret;
4. message_new text;
5. command;
6. location;
7. message_event callback;
8. callback answer;
9. ambiguous city;
10. lead pagination;
11. messages.edit;
12. unique `random_id` on retry;
13. photo upload;
14. document upload;
15. retry on temporary API/network errors;
16. duplicate callback event;
17. общий profile contract совпадает с Telegram/MAX.

Live VK test с секретами не делать обязательным CI job. Для production readiness нужен отдельный ручной smoke с тестовым сообществом.

## Definition of Done

VK gateway готов, когда пользователь проходит базовые прогнозные flow без знания Telegram-синтаксиса, получает тот же common result, native keyboard/location работают, status не спамит чат, вложения отправляются штатными upload flow, Callback API защищён и VK-specific код не содержит метеорологических вычислений.
