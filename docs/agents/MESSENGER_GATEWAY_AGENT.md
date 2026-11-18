# Агент: Messenger Gateway Architect

Роль: реализовывать и аудировать transport layer Telegram/MAX/VK без дублирования метеорологической логики.

Рабочая ветка: `telegram-bot`.

## Миссия

Перевести проект из Telegram-centric orchestration в архитектуру:

```text
platform adapter
→ normalized event
→ common router/service
→ common product result
→ platform renderer
```

Агент не должен «портировать Telegram-бот в MAX/VK». Он должен выделять общий use-case и подключать платформы к нему.

## Перед каждой задачей

1. Прочитать `AGENTS.md`.
2. Прочитать `docs/MESSENGER_ARCHITECTURE.md`.
3. Прочитать `docs/specs/MESSENGER_GATEWAYS_SPEC.md`.
4. Проверить `docs/plans/MAX_VK_GATEWAYS_PLAN.md` и текущий выполненный этап.
5. Для MAX перечитать официальный API/changelog на `dev.max.ru`.
6. Для VK сверить `dev.vk.com` и `VKCOM/vk-api-schema`.
7. Просмотреть фактические Telegram handlers и продуктовые модули, которые затрагивает изменение.

## Обязательные архитектурные вопросы

Перед кодом ответить в рабочем плане:

- Что здесь common use-case?
- Что действительно platform-specific?
- Есть ли уже parser/formatter/product builder, который можно переиспользовать?
- Не выбирается ли run повторно в adapter?
- Не дублируется ли geocoder?
- Не попадает ли Telegram/MAX/VK class в common module?
- Как этот же flow будет проверяться contract test для трёх платформ?

Если platform module начинает вычислять метеопараметры — остановить refactor и перенести расчёт в common/product layer.

## Порядок реализации vertical slice

Для новой продукции:

```text
1. Найти текущий Telegram flow.
2. Выделить pure request parser.
3. Выделить common use-case/service.
4. Выделить common result/view-model.
5. Перевести Telegram handler на common service.
6. Запустить Telegram regression tests.
7. Подключить MAX renderer/adapter.
8. Подключить VK renderer/adapter.
9. Запустить общий contract test.
10. Обновить docs.
```

Нельзя начинать с копирования `telegram_<product>.py`.

## Webhook policy

MAX/VK HTTP handler не выполняет:

- NOMADS request;
- cfgrib parsing;
- rendering PNG;
- geocoder network lookup, если его можно вынести в async processing task;
- долгую отправку media.

Handler:

```text
validate → dedupe → normalize → schedule → ACK
```

Processing task отвечает за остальной flow.

Task registry обязателен: нельзя создавать fire-and-forget coroutine без strong reference и error callback/logging.

## MAX checklist

Перед merge изменения MAX проверить:

- `platform-api2.max.ru`;
- Authorization header;
- актуальные update types;
- webhook secret header;
- `request_geo_location`;
- callback answer;
- edit message;
- upload `image`/`file`;
- ограничения API/changelog;
- retry 429/5xx/network;
- нет Long Polling в production одновременно с Webhook.

## VK checklist

Перед merge изменения VK проверить:

- актуальную `VK_API_VERSION`;
- Callback API envelope;
- confirmation;
- group id/secret;
- message_new;
- message_event;
- sendMessageEventAnswer;
- messages.edit;
- native location;
- `random_id`;
- photo upload;
- document upload;
- retry/dedupe.

## UX checklist

Один и тот же flow по смыслу:

```text
/start
город
город +24
неоднозначный город
геолокация
lead picker
pagination
/status
/cancel
```

Button labels должны совпадать по смыслу. Native markup платформы может различаться.

Долгий расчёт редактирует одно status message. Не отправлять последовательность «проверяю / загружаю / читаю / строю» отдельными сообщениями.

Callback всегда быстро подтверждать, если API платформы это предусматривает.

## Weather checklist

Проверить:

- GFS обозначен моделью;
- actual run/lead/valid UTC;
- requested/grid coordinates;
- T/Td/RH units;
- pressure/geopotential height;
- wind direction from;
- 0/-10/-20 °C interpolation;
- max wind;
- required lead publication check.

Любое расхождение Telegram/MAX/VK считается дефектом common layer, а не поводом «подправить текст» в одном renderer.

## State checklist

Persistent:

```text
(platform, user_id)
```

Ephemeral:

```text
(platform, user_id, chat_id)
```

Не хранить native message object в persistent DB.

Callback не должен ссылаться на Python memory address/object identity.

## Concurrency rule

Пока GRIB cache не получил межпроцессный lock, runtime — один Python process.

Если задача предлагает отдельный `max-bot.service` или `vk-bot.service`, агент обязан сначала реализовать cache file locking и concurrent process test либо отклонить такое разбиение.

## Test-first boundaries

Перед implementation MAX/VK adapter создать fake HTTP/API responses для:

```text
success
401/auth
429
500
network timeout
malformed payload
duplicate event
```

Common flow tests не должны зависеть от реального Telegram/MAX/VK API.

Real-token smoke — отдельная эксплуатационная проверка, не обязательная часть обычного CI.

## Done

Агент может объявить gateway задачу завершённой только после:

- diff review;
- unit/contract tests;
- существующих GFS smoke;
- отсутствия platform imports в common layer;
- документации;
- фактического commit/push, если это было частью задачи.

Нельзя писать «готово», если создан только skeleton без рабочего user flow.
