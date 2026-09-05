# Deploy GFS Profile: Telegram + MAX + VK

Рабочая ветка — `telegram-bot`. Установленное приложение работает из `/opt/gfs_profile` одним systemd-процессом:

```text
systemd
  ↓
messenger_launcher.py
  ├─ Telegram long polling
  ├─ MAX  POST /webhooks/max
  ├─ VK   POST /webhooks/vk
  └─ web/API
```

`MESSENGER_RUNTIME_ENABLED=1` — штатный production mode. `0` оставлен как аварийный Telegram-only fallback без смены systemd unit.

Пошаговое создание ботов и получение token/group id: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md).

## Базовая установка

```bash
bash install_telegram_bot.sh
```

Telegram и web/API могут работать без MAX/VK token.

## Первичная настройка MAX/VK

После базовой установки и настройки публичного HTTPS reverse proxy используйте мастер:

```bash
sudo bash setup_messenger_bots.sh --max
sudo bash setup_messenger_bots.sh --vk
sudo bash setup_messenger_bots.sh --max --vk
```

Мастер требует минимальный набор:

```text
MAX: MAX_BOT_TOKEN + MAX_WEBHOOK_URL
VK:  VK_BOT_TOKEN + VK_GROUP_ID + VK_CALLBACK_URL
```

Он автоматически:

```text
генерирует MAX_WEBHOOK_SECRET
генерирует VK_CALLBACK_SECRET
получает VK_CONFIRMATION_CODE через VK API
проверяет env
запускает штатный deploy
ждёт /ready
регистрирует MAX subscription и VK Callback API server
проверяет фактическую регистрацию
```

Status без изменений:

```bash
sudo bash setup_messenger_bots.sh --status
```

## Ручное обновление

```bash
cd ~/gfs_profile
git checkout telegram-bot
git pull --ff-only
sudo bash deploy_telegram_bot.sh --yes
```

Deploy автоматически мигрирует старый unit `telegram_bot.py` на `messenger_launcher.py`.

## Production deploy sequence

```text
1. deploy lock
2. сохранить .env/.cache_gfs/.venv/.install-state
3. checkout → /opt/gfs_profile
4. Python dependencies
5. unit tests
6. runtime_check.py
7. messenger_config_check.py
8. DaData preflight
9. systemd unit
10. restart
11. GET /ready
12. Telegram commands
13. MAX/VK webhook registration
14. .install-state
```

Webhook регистрируется только после `/ready`.

## Runtime env

```env
MESSENGER_RUNTIME_ENABLED=1
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081
MESSENGER_RUNTIME_LOG_LEVEL=info
MESSENGER_RUNTIME_ACCESS_LOG=0

MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=
MAX_WEBHOOK_SECRET=

VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_URL=
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
```

Примеры публичных URL:

```text
https://bot.example.ru/webhooks/max
https://bot.example.ru/webhooks/vk
```

Не записывайте example URL в `.env`, если соответствующая платформа не настраивается.

## Ручная подготовка secrets/code

Если `.env` заполнен вручную минимальными platform-реквизитами:

```bash
cd /opt/gfs_profile
sudo .venv/bin/python prepare_messenger_config.py --env-file .env
```

После этого:

```bash
sudo bash deploy_telegram_bot.sh --yes
```

## Health/readiness

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/ready
```

`/health` показывает платформы/common products/resource limits без secrets. `/ready` возвращает 200 только после полного запуска Telegram application/runtime.

## Общие лимиты

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Лимиты process-wide: Telegram/MAX/VK/web делят одни permits. Redis/Celery не используются.

## Регистрация и status

Штатный deploy после `/ready` вызывает:

```bash
python register_messenger_webhooks.py
```

Проверка без изменения provider state:

```bash
cd /opt/gfs_profile
set -a
source .env
set +a
.venv/bin/python register_messenger_webhooks.py --status
```

Отдельно:

```bash
.venv/bin/python register_messenger_webhooks.py --max --status
.venv/bin/python register_messenger_webhooks.py --vk --status
```

Status проверяет не только локальный runtime:

- MAX — наличие subscription для точного URL и update types;
- VK — confirmation code, точный Callback server URL, `message_new` и `message_event`.

## Public HTTPS

Внутренний uvicorn обычно слушает:

```text
127.0.0.1:8081
```

Nginx/HAProxy публикует только `/webhooks/max` и `/webhooks/vk` через HTTPS с доверенным сертификатом. Пример есть в `docs/MESSENGER_REGISTRATION.md`.

## Автообновление

```bash
sudo bash install_auto_update.sh --yes
sudo bash install_auto_update.sh --status
```

Updater следит за `origin/telegram-bot`, использует lock и вызывает тот же `deploy_telegram_bot.sh`; при провале возвращает предыдущий installed SHA.

## Locks

Deploy:

```text
/run/lock/gfs-profile-bot.deploy.lock
```

Auto-update:

```text
/run/lock/gfs-profile-bot-auto-update.lock
```

## Сохраняемые данные

`rsync --delete` исключает:

```text
.env
.install-state
.venv/
.cache_gfs/
data/basemap/
```

Сохраняются admin stats, Telegram preferences/recipes, MAX/VK recipes, schedules, GFS/geocode/meteogram cache и platform secrets.

## Проверка после deploy

```bash
sudo bash deploy_telegram_bot.sh --status
sudo bash setup_messenger_bots.sh --status
sudo systemctl status gfs-profile-bot.service
sudo systemctl cat gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8081/ready
```

Effective unit:

```text
ExecStart=/opt/gfs_profile/.venv/bin/python /opt/gfs_profile/messenger_launcher.py
```

До push/merge:

```bash
python -m unittest discover -s tests
python runtime_check.py
python -m gfs_core --lat 45.0355 --lon 38.9753 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
```

## Deploy options

```text
--yes
--install-system-packages
--skip-pip
--skip-tests
--skip-commands
--skip-webhooks
--no-restart
--status
```

`--skip-webhooks` — только для диагностики/reverse-proxy preparation.

## Emergency switch

```bash
sudo bash install_messenger_runtime.sh --status
sudo bash install_messenger_runtime.sh --disable
sudo bash install_messenger_runtime.sh --enable
```

Обычная установка/deploy не требуют отдельного запуска этого helper.
