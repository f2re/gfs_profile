# Multi-messenger runtime: Telegram + MAX + VK

Статус: реализован gateway/profile vertical slice. По умолчанию выключен до настройки публичного HTTPS и platform credentials.

## Архитектура запуска

Один systemd service и один Python process обслуживают:

```text
Telegram polling
MAX  POST /webhooks/max
VK   POST /webhooks/vk
web/API
```

Несколько uvicorn workers запрещены: текущий GRIB cache использует process-local locking и общий `.part` файл. `messenger_launcher.py` всегда запускает ASGI с `workers=1`.

## Безопасный режим по умолчанию

```env
MESSENGER_RUNTIME_ENABLED=0
```

В этом режиме launcher вызывает прежний `telegram_bot.main()`; поведение существующего Telegram-бота не меняется.

При `MESSENGER_RUNTIME_ENABLED=1` launcher запускает `messenger_runtime:app`, который внутри того же процесса поднимает Telegram polling и webhook endpoints MAX/VK.

## Публичный HTTPS

Runtime по умолчанию слушает только loopback:

```env
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081
```

Снаружи нужен Nginx/HAProxy с доверенным TLS-сертификатом. Для MAX публичный endpoint обязан быть HTTPS на 443, например:

```text
https://bot.example.ru/webhooks/max -> http://127.0.0.1:8081/webhooks/max
https://bot.example.ru/webhooks/vk  -> http://127.0.0.1:8081/webhooks/vk
```

Не публиковать внутренний порт `8081` напрямую.

## Переменные MAX

```env
MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
MAX_WEBHOOK_SECRET=
```

Используется `https://platform-api2.max.ru`. Токен передаётся только заголовком `Authorization`. Webhook проверяет `X-Max-Bot-Api-Secret`.

## Переменные VK

```env
VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
```

`VK_CONFIRMATION_CODE` должен совпадать с `groups.getCallbackConfirmationCode` для указанного сообщества.

## Миграция существующей systemd-установки

После штатного deploy новой ветки сначала проверьте состояние:

```bash
sudo bash install_messenger_runtime.sh --status
```

`--status` ничего не меняет. При первом `--enable` или `--disable` скрипт устанавливает systemd drop-in, который заменяет только `ExecStart`:

```text
python /opt/gfs_profile/messenger_launcher.py
```

Оригинальный unit и `.env` не перезаписываются целиком.

Для включения multi-messenger runtime после настройки reverse proxy и `.env`:

```bash
sudo bash install_messenger_runtime.sh --enable
```

Порядок:

1. проверка установленного кода/venv/env;
2. проверка обязательных переменных настроенных платформ;
3. установка systemd drop-in;
4. `MESSENGER_RUNTIME_ENABLED=1`;
5. `runtime_check.py` от service user с `.env`;
6. restart одного systemd service;
7. публичный webhook preflight;
8. регистрация MAX/VK webhook через официальные API.

Если регистрация API не проходит, runtime endpoint остаётся активным для безопасного повторного запуска регистрации; Telegram внутри runtime продолжает работать.

Вернуть Telegram-only режим:

```bash
sudo bash install_messenger_runtime.sh --disable
```

Drop-in остаётся, но launcher при flag `0` запускает старый Telegram polling.

## Ручная регистрация webhook

Если runtime уже поднят:

```bash
cd /opt/gfs_profile
set -a
source .env
set +a
.venv/bin/python register_messenger_webhooks.py
```

Только MAX или VK:

```bash
.venv/bin/python register_messenger_webhooks.py --max
.venv/bin/python register_messenger_webhooks.py --vk
```

По умолчанию регистрация сначала проверяет публичный HTTPS endpoint. `--no-probe` предназначен только для диагностики и не рекомендуется для production.

MAX: читаются существующие `GET /subscriptions`, затем `POST /subscriptions` создаёт или обновляет URL с `bot_started`, `message_created`, `message_callback`.

VK: проверяется confirmation code, переиспользуется существующий callback server с тем же URL либо создаётся новый, затем включаются `message_new` и `message_event`.

## Реализованный пользовательский паритет

На текущем этапе общий gateway поддерживает реальный `/profile` vertical slice:

- `/start`;
- город/координаты;
- `Москва` -> выбор срока;
- `Москва +24` -> расчёт;
- неоднозначный город -> callback-выбор;
- native location;
- быстрые +0/+3/+6/+12/+24/+48;
- страницы сроков до +384;
- `/status`, `/cancel`;
- одно редактируемое status message;
- общий GFS run selection;
- одинаковую сводку и PNG/CSV.

Telegram `/profile` уже использует тот же `messenger/profile_service.py`.

Следующий продуктовый этап: вынести и подключить к тому же contract `/aero`, `/windgram`, `/cloudgram`, `/meteogram`, `/map`, затем `/route`, `/settings`, `/schedule`.
