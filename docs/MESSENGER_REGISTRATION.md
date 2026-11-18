# Регистрация и первичная настройка MAX и VK

Документ описывает production-настройку существующего GFS Profile после базовой установки Telegram-бота. Пользователь не должен вручную создавать webhook/subscription через curl: проект умеет подготовить секреты, получить VK confirmation code, зарегистрировать MAX subscription и VK Callback API server сам.

## 0. Что должно быть готово на сервере

Сначала установите проект обычным способом:

```bash
bash install_telegram_bot.sh
```

После установки должны существовать:

```text
/opt/gfs_profile/.env
/opt/gfs_profile/.venv/bin/python
/etc/systemd/system/gfs-profile-bot.service
```

Production entrypoint — `messenger_launcher.py`. При `MESSENGER_RUNTIME_ENABLED=1` один process обслуживает Telegram polling, MAX/VK webhook и web/API.

Публичный домен должен иметь доверенный HTTPS-сертификат. Runtime по умолчанию слушает только loopback:

```text
127.0.0.1:8081
```

Рекомендуемые публичные URL:

```text
https://bot.example.ru/webhooks/max
https://bot.example.ru/webhooks/vk
```

Пример Nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name bot.example.ru;

    ssl_certificate     /etc/letsencrypt/live/bot.example.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.ru/privkey.pem;

    location /webhooks/ {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

После изменения Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 1. MAX: создание и получение token

По актуальной документации MAX создание чат-ботов доступно через платформу MAX для партнёров после создания и верификации профиля организации, ИП или самозанятого. Профиль должен соответствовать требованиям платформы; каждый бот проходит модерацию.

Официальные разделы:

```text
https://dev.max.ru/docs/chatbots/bots-create/create
https://dev.max.ru/docs/chatbots/bots-create/manage
https://dev.max.ru/docs-api/methods/POST/subscriptions
```

### Шаги в MAX

1. Откройте платформу MAX для партнёров.
2. Создайте/верифицируйте профиль организации, ИП или самозанятого.
3. Откройте `Чат-боты` → `Создать`.
4. Заполните карточку бота и отправьте на модерацию.
5. После успешной модерации откройте нужного бота.
6. Перейдите `Расширенные настройки` → `Настроить`.
7. Скопируйте значение поля `Токен`.

Токен является паролем бота. Не публикуйте его и не коммитьте в Git.

### Что нужно проекту от MAX

Вручную нужны только два значения:

```env
MAX_BOT_TOKEN=<token из Расширенные настройки → Настроить>
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
```

`MAX_WEBHOOK_SECRET` можно не придумывать и не копировать. Если он пустой, `prepare_messenger_config.py` создаст криптографически случайный URL-safe secret и сохранит его в `/opt/gfs_profile/.env`.

Проект регистрирует subscription через MAX API сам. Token передаётся только в HTTP header `Authorization`; query-параметр `access_token` не используется. При подписке указываются события:

```text
bot_started
message_created
message_callback
```

Если secret указан в subscription, MAX возвращает его в заголовке:

```text
X-Max-Bot-Api-Secret
```

Endpoint проекта проверяет этот заголовок до обработки update.

Production использует Webhook. Не запускайте параллельно отдельный Long Polling `/updates` процесс для того же бота.

## 2. VK: создание community bot и token

Для VK используется Community Bot + Callback API.

### Шаги в VK

1. Создайте сообщество VK или выберите существующее, где у вас есть права администратора.
2. В управлении сообществом включите сообщения сообщества (`Сообщения` / `Сообщения сообщества`).
3. Откройте `Управление` → `Работа с API` (`API usage`).
4. На вкладке `Ключи доступа` / `Access tokens` создайте ключ сообщества.
5. Дайте ключу права, необходимые проекту:
   - отправка/редактирование сообщений;
   - загрузка фотографий;
   - загрузка документов;
   - управление Callback API/сообществом, если этот доступ показан отдельным переключателем.
6. Скопируйте community access token.
7. Узнайте числовой ID сообщества. В `.env` он записывается положительным числом без `-`.

Код проекта использует VK API методы:

```text
messages.send
messages.edit
messages.sendMessageEventAnswer
photos.getMessagesUploadServer
photos.saveMessagesPhoto
docs.getMessagesUploadServer
docs.save
groups.getCallbackConfirmationCode
groups.getCallbackServers
groups.addCallbackServer
groups.getCallbackSettings
groups.setCallbackSettings
```

### Что нужно проекту от VK

Вручную нужны три значения:

```env
VK_BOT_TOKEN=<community access token>
VK_GROUP_ID=123456789
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
```

Следующие поля пользователь может оставить пустыми:

```env
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
```

Проект заполнит их сам:

- `VK_CALLBACK_SECRET` — случайный локальный secret;
- `VK_CONFIRMATION_CODE` — фактический код из `groups.getCallbackConfirmationCode` для указанного `VK_GROUP_ID`.

После запуска runtime `register_messenger_webhooks.py`:

1. повторно сверяет confirmation code с VK API;
2. делает контрольный `confirmation` POST на публичный callback URL;
3. находит существующий callback server с тем же URL;
4. если server отсутствует — вызывает `groups.addCallbackServer`;
5. включает `message_new=1` и `message_event=1` через `groups.setCallbackSettings`.

То есть вручную добавлять URL в разделе Callback API не требуется. Это остаётся допустимым диагностическим вариантом, но штатная установка делает регистрацию через API.

## 3. Рекомендуемый мастер настройки

После получения platform tokens запустите из checkout ветки `telegram-bot`:

```bash
sudo bash setup_messenger_bots.sh --max --vk
```

Можно настроить одну платформу:

```bash
sudo bash setup_messenger_bots.sh --max
sudo bash setup_messenger_bots.sh --vk
```

Мастер интерактивно запросит только обязательные значения.

Неинтерактивный MAX:

```bash
sudo env \
  MAX_BOT_TOKEN='<MAX_TOKEN>' \
  MAX_WEBHOOK_URL='https://bot.example.ru/webhooks/max' \
  bash setup_messenger_bots.sh --max --yes
```

Неинтерактивный VK:

```bash
sudo env \
  VK_BOT_TOKEN='<VK_COMMUNITY_TOKEN>' \
  VK_GROUP_ID='123456789' \
  VK_CALLBACK_URL='https://bot.example.ru/webhooks/vk' \
  bash setup_messenger_bots.sh --vk --yes
```

При настройке обеих платформ передайте оба набора переменных и используйте `--max --vk --yes`.

### Что мастер делает сам

```text
ввод token / group id / public URL
→ запись в /opt/gfs_profile/.env
→ генерация MAX_WEBHOOK_SECRET
→ генерация VK_CALLBACK_SECRET
→ groups.getCallbackConfirmationCode
→ запись VK_CONFIRMATION_CODE
→ messenger_config_check.py
→ штатный deploy_telegram_bot.sh
→ restart messenger_launcher.py
→ GET /ready
→ регистрация Telegram commands
→ MAX POST /subscriptions
→ VK add/update Callback API server
→ проверка фактического статуса регистрации
```

Secrets не выводятся в лог в открытом виде.

## 4. Ручная настройка `.env`

Если мастер использовать не нужно, отредактируйте:

```text
/opt/gfs_profile/.env
```

Минимальный пример:

```env
MESSENGER_RUNTIME_ENABLED=1
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081

MAX_BOT_TOKEN=<MAX_TOKEN>
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
MAX_WEBHOOK_SECRET=

VK_BOT_TOKEN=<VK_COMMUNITY_TOKEN>
VK_GROUP_ID=123456789
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
```

После ручного заполнения обязательных полей:

```bash
sudo /opt/gfs_profile/.venv/bin/python \
  prepare_messenger_config.py \
  --env-file /opt/gfs_profile/.env
```

Если команда запускается из checkout, используйте файл `prepare_messenger_config.py` из той же версии ветки.

После этого выполняйте штатный deploy.

## 5. Проверка после deploy

### Runtime

```bash
curl -fsS http://127.0.0.1:8081/ready
curl -fsS http://127.0.0.1:8081/health
```

В `/health` должны быть видны включённые платформы без token/secret значений.

### MAX/VK API registration

Из установленного каталога:

```bash
cd /opt/gfs_profile
set -a
source .env
set +a
.venv/bin/python register_messenger_webhooks.py --status
```

Или из checkout:

```bash
sudo bash setup_messenger_bots.sh --status
```

Ожидаемый смысл ответа:

```text
OK MAX: подписка активна · https://bot.example.ru/webhooks/max
OK VK: callback server активен · id=... · https://bot.example.ru/webhooks/vk
```

### systemd

```bash
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

## 6. Проверка пользовательского flow

В MAX и VK после регистрации проверьте:

```text
/start
/profile Москва +24
/aero Москва +24
/windgram Москва to=120 step=6 param=wind
/cloudgram Москва to=72 step=3 mode=pro
```

Затем отдельно:

```text
неоднозначный город
native geo/location
callback-кнопки
repeat saved recipe
pin/unpin recipe
/status
/cancel
```

## 7. Типовые ошибки

### MAX: token не работает

Проверьте, что бот успешно прошёл модерацию и token взят именно из `Чат-боты → нужный бот → Расширенные настройки → Настроить`.

### MAX: webhook недоступен

Проверьте публичный HTTPS, DNS, Nginx/HAProxy, сертификат и путь, указанный в `MAX_WEBHOOK_URL`. HTTP и самоподписанный сертификат для production не использовать.

### VK: `groups.getCallbackConfirmationCode` возвращает ошибку

Обычно неверны `VK_BOT_TOKEN`, `VK_GROUP_ID` или права community token. ID задаётся без минуса.

### VK: server зарегистрирован, но сообщений нет

Проверьте:

- включены сообщения сообщества;
- `register_messenger_webhooks.py --status` видит `message_new` и `message_event`;
- публичный URL доступен из интернета;
- `VK_CALLBACK_SECRET` в `.env` не менялся после регистрации без повторного deploy/register.

### `/ready` работает, но platform status не OK

Runtime и регистрация — разные проверки. `/ready` означает, что локальный multi-messenger process поднялся. `register_messenger_webhooks.py --status` подтверждает, что внешняя платформа действительно направляет события на нужный URL.

## 8. Безопасность

Никогда не коммитьте:

```text
TELEGRAM_BOT_TOKEN
MAX_BOT_TOKEN
MAX_WEBHOOK_SECRET
VK_BOT_TOKEN
VK_CALLBACK_SECRET
VK_CONFIRMATION_CODE
DADATA_API_KEY
```

`.env` хранится с ограниченными правами и исключён из deploy rsync/delete. При подозрении на компрометацию token отзовите/обновите его на соответствующей платформе и повторите setup/deploy.
