# AGENTS.md — правила разработки gfs_profile

Рабочая ветка для Telegram/MAX/VK: `telegram-bot`.

## 1. Главный принцип

Проект имеет одно метеорологическое ядро и несколько транспортов: Telegram, MAX, VK и web/API.

Новая функция не должна реализовываться отдельной копией расчётов для каждого мессенджера. Целевая схема:

`platform event → adapter → normalized event → common use-case/service → common result/view-model → platform renderer`.

`gfs_core.py`, продуктовые модули, geocoder, выбор фактического GFS run, диагностические расчёты, formatter и правила продукции не зависят от платформы.

Запрещено:

- копировать `telegram_*` в `max_*`/`vk_*` вместе с метеорологической логикой;
- выбирать GFS run отдельно в каждом мессенджере;
- делать разные формулы, подписи единиц или критерии диагностики по платформам;
- добавлять Redis/Celery/внешнюю БД/очередь без доказанной необходимости;
- выдавать GFS за наблюдение или радиозонд.

## 2. Перед правкой

1. Проверить фактический HEAD `telegram-bot`.
2. Изучить текущий flow Telegram и общий продуктовый код.
3. Для MAX перед существенной правкой сверить `https://dev.max.ru`, API reference и changelog.
4. Для VK сверить `https://dev.vk.com`, актуальную версию API и официальную схему `VKCOM/vk-api-schema`.
5. Определить, относится изменение к common service, platform adapter или renderer.
6. Сначала исправлять common слой; platform-specific код должен оставаться тонким.

Цикл: `аудит → план → правка → тесты → smoke → diff → commit → push`.

## 3. Целевая структура messenger layer

Планируемая структура:

```text
messenger/
  contracts.py
  events.py
  router.py
  service.py
  state.py
  formatter.py
  telegram/
    adapter.py
    renderer.py
  max/
    api.py
    adapter.py
    webhook.py
    renderer.py
  vk/
    api.py
    adapter.py
    webhook.py
    renderer.py
```

Точные имена могут меняться, но границы ответственности обязательны.

Common service получает нормализованный запрос и возвращает common result. Renderer отвечает только за ограничения конкретной платформы: кнопки, разметку, media upload, edit status, callback answer.

## 4. Обязательный паритет

Базовый релиз MAX/VK должен повторять ключевые Telegram-flow:

- `/start`, `/help`, `/status`, `/cancel`;
- город или координаты;
- `Москва` → выбор срока;
- `Москва +24` → немедленный расчёт;
- неоднозначный город → native callback-выбор;
- геолокация → выбор срока;
- быстрые `+0,+3,+6,+12,+24,+48`;
- все сроки до `+384` с пагинацией;
- `/profile`, `/aero`, `/windgram`, `/cloudgram`, `/meteogram`, `/map`;
- PNG/CSV и другие уже поддерживаемые форматы продукта;
- одно редактируемое status message для долгой операции.

`/route`, `/settings`, `/schedule` переносятся после базового gateway parity, но архитектура сразу должна позволять их подключить без нового ядра.

Если функция временно отсутствует на одной платформе, это явно документируется. Молчаливый Telegram-only запрещён.

## 5. Нормализованные события и действия

Adapter обязан привести вход к общей модели как минимум с полями:

`platform, event_type, user_id, chat_id, message_id, text, command, callback_payload, location, raw_event_id`.

Общие исходящие операции:

`send_text`, `edit_text`, `send_image`, `send_file`, `send_animation`, `answer_callback`.

Общие button actions:

`callback`, `request_location`, `text`, `link`.

Callback payload должен быть компактным, versioned и по возможности stateless: из него или из устойчивого state key должны восстанавливаться `product/action/lead/location/page/parameters`. Нельзя полагаться только на объект в памяти процесса.

## 6. Webhook и concurrency

Webhook MAX/VK должен быстро проверить запрос, нормализовать событие, зарегистрировать задачу внутри приложения и вернуть успешный HTTP-ответ. GFS/NOMADS/рендер PNG нельзя выполнять внутри HTTP request lifecycle.

На первом этапе Telegram polling, MAX Webhook и VK Callback API запускаются в одном Python-процессе. Причина: текущий GRIB cache использует process-local download lock и общий `<cache-key>.part`; несколько процессов с одним `.cache_gfs` могут конфликтовать.

Разделять процессы разрешено только после межпроцессной блокировки cache key (`flock`/эквивалент) и отдельного теста гонок.

Долгая операция редактирует одно status message. Retry/backoff обязателен для `429`, `5xx` и сетевых ошибок платформ.

## 7. MAX

Актуальная базовая схема на 2026-09-04:

- API: `https://platform-api2.max.ru`;
- токен только в `Authorization`;
- production: Webhook, Long Polling только разработка/диагностика;
- Webhook: HTTPS с доверенным TLS;
- `POST /subscriptions` с `secret`;
- проверять `X-Max-Bot-Api-Secret`;
- события минимум `bot_started`, `message_created`, `message_callback`;
- native `request_geo_location`;
- редактирование status через messages API;
- media: `/uploads` → upload URL/token → `/messages`;
- для изображения использовать `type=image`, не устаревший `photo`;
- callback подтверждать быстро;
- учитывать актуальные rate limits и changelog.

Подробно: `MAX_BOT.md` и `docs/specs/MESSENGER_GATEWAYS_SPEC.md`.

## 8. VK

Целевая интеграция: Community Bot + Callback API.

Минимум:

- `confirmation`;
- `message_new`;
- `message_event`;
- проверка group id/secret;
- `messages.send`, `messages.edit`;
- `messages.sendMessageEventAnswer`;
- native location button/location attachment;
- image upload через messages photo upload flow;
- CSV/DOCX/PDF через document upload flow;
- уникальный `random_id` для отправки;
- версия VK API задаётся конфигурацией и перед реализацией сверяется с официальной схемой.

Подробно: `VK_BOT.md`.

## 9. Метеорологическая корректность

Во всех платформах одинаково показывать:

- фактический run/cycle UTC, lead и valid UTC;
- requested point и GFS grid point;
- p/z, T/Td, RH;
- направление ветра — откуда дует — и скорость;
- изотермы 0/-10/-20 °C;
- max wind и число уровней, где применимо;
- маркировку `GFS 0.25° grid • модельный прогноз, не радиозонд и не наблюдение`.

Новый цикл нельзя выбирать только по `f000`: проверять требуемый lead и при необходимости откатываться к предыдущему опубликованному run.

## 10. State, статистика и секреты

Устойчивый ключ пользователя: `(platform, user_id)`, при необходимости с `chat_id` для session-state.

Не создавать три разных схемы preferences. Цель — общая SQLite schema с колонкой `platform`. Автоматически связывать одного человека между Telegram/MAX/VK не требуется.

Статистика также хранит `platform`.

Секреты только в `.env`. Не коммитить токены, webhook secret, VK confirmation code и callback secret.

## 11. Проверки

Перед push минимум:

```bash
python -m unittest discover -s tests
python -m gfs_core --lat 45.0355 --lon 38.9753 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
```

Для messenger layer обязательны contract tests одного сценария через fake Telegram/MAX/VK adapters.

MAX отдельно: webhook secret, callback, geo, media upload, edit status, retry/rate-limit.

VK отдельно: confirmation/secret, `message_new`, `message_event`, geo, photo/doc upload, edit status, retry и `random_id`.

Не добавлять тяжёлый CI ради формальности. Проверки должны ловить реальные regressions.

## 12. Документация и Definition of Done

Изменение messenger layer сопровождается актуализацией `README.md`, `TELEGRAM_BOT.md`, `MAX_BOT.md`, `VK_BOT.md`, а deploy/env — ещё и `DEPLOY.md`/env example.

Gateway-фича готова, если один и тот же use-case работает во всех заявленных платформах, расчёты не дублируются, ошибки понятны, tests/smoke пройдены, diff просмотрен, а документация соответствует коду.
