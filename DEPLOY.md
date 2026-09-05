# Deploy GFS Profile: Telegram + MAX + VK

Рабочая ветка — `telegram-bot`. Установленное приложение работает из `/opt/gfs_profile` одним systemd-процессом.

```text
systemd
  ↓
messenger_launcher.py
  ├─ Telegram long polling
  ├─ MAX  POST /webhooks/max
  ├─ VK   POST /webhooks/vk
  └─ web/API
```

`messenger_launcher.py` является штатным `ExecStart`. При `MESSENGER_RUNTIME_ENABLED=1` запускается один uvicorn worker и общий runtime. Значение `0` оставлено только как аварийный Telegram-only fallback без изменения systemd unit.

## Ручное обновление

```bash
cd ~/gfs_profile
git checkout telegram-bot
git pull --ff-only
sudo bash deploy_telegram_bot.sh --yes
```

Deploy автоматически мигрирует старый unit, который запускал `telegram_bot.py`, на `messenger_launcher.py`.

## Порядок production deploy

Штатный deploy выполняет:

```text
1. lock от параллельного deploy
2. сохранение .env/.cache_gfs/.venv/.install-state
3. синхронизацию checkout → /opt/gfs_profile
4. зависимости Python
5. unit tests
6. runtime_check.py
7. messenger_config_check.py
8. DaData preflight
9. запись systemd unit с messenger_launcher.py
10. restart
11. локальный GET /ready
12. регистрация Telegram commands
13. проверка/регистрация MAX/VK webhook
14. запись .install-state
```

Webhook регистрируются только после успешного `/ready`. Поэтому deploy не создаёт подписку на ещё не запущенный endpoint.

## Конфигурация runtime

```env
MESSENGER_RUNTIME_ENABLED=1
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081
MESSENGER_RUNTIME_LOG_LEVEL=info
MESSENGER_RUNTIME_ACCESS_LOG=0

MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
MAX_WEBHOOK_SECRET=

VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
```

MAX/VK необязательны: пустой токен отключает соответствующую платформу, но Telegram и web/API продолжают работать через тот же launcher.

Публичный HTTPS завершается Nginx/HAProxy. Внутренний uvicorn по умолчанию слушает только `127.0.0.1:8081`.

## Health/readiness

После запуска доступны:

```text
GET /health   процесс жив, показывает включённые платформы и лимиты
GET /ready    200 только после запуска Telegram application; иначе 503
```

Проверка на сервере:

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/ready
```

`/health` не содержит токены или secrets.

## Общие лимиты ресурсов

Telegram, MAX, VK и смонтированный web/API используют один `RuntimeResources`:

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Это реальные process-wide лимиты. Например, при `MAX_CONCURRENT_GFS=2` один запрос Telegram и один MAX могут выполняться одновременно, но третий GFS-запрос ждёт свободный permit независимо от платформы.

Внешние Redis/Celery/очереди для этого не используются.

## Регистрация webhook

После успешного restart deploy запускает:

```bash
python register_messenger_webhooks.py
```

Если MAX/VK токены не заданы, команда завершается успешно без действий. Если платформа настроена, проверяются публичный HTTPS endpoint и фактическая регистрация у провайдера.

Ручной повтор:

```bash
cd /opt/gfs_profile
sudo -u gfsbot env $(grep -v '^#' .env | xargs) .venv/bin/python register_messenger_webhooks.py
```

На практике безопаснее загрузить `.env` через shell с `set -a; source .env; set +a`, а не печатать secrets в командной строке.

## Автоматическое обновление

Рекомендуемый режим — существующий systemd timer:

```bash
sudo bash install_auto_update.sh --yes
sudo bash install_auto_update.sh --status
```

Updater следит за `origin/telegram-bot`, использует отдельный lock, вызывает тот же `deploy_telegram_bot.sh`, а при провале возвращает предыдущий установленный SHA. Значит multi-messenger preflight, `/ready` и webhook registration применяются и к автоматическому обновлению.

## Locks

Штатный deploy:

```text
/run/lock/gfs-profile-bot.deploy.lock
```

Auto-update:

```text
/run/lock/gfs-profile-bot-auto-update.lock
```

Если `/run/lock` недоступен, deploy использует lock внутри git-dir. Предсказуемый `/tmp/...lock` больше не используется.

## Сохраняемые данные

`rsync --delete` исключает:

```text
.env
.install-state
.venv/
.cache_gfs/
data/basemap/
```

Поэтому сохраняются:

- admin stats SQLite;
- Telegram preferences/recipes;
- MAX/VK recipes;
- расписания;
- GFS/geocode/meteogram cache;
- локальная конфигурация и secrets.

## Проверка после deploy

```bash
sudo bash deploy_telegram_bot.sh --status
sudo systemctl status gfs-profile-bot.service
sudo systemctl cat gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8081/ready
```

В effective unit ожидается:

```text
ExecStart=/opt/gfs_profile/.venv/bin/python /opt/gfs_profile/messenger_launcher.py
```

До push/merge обязательны:

```bash
python -m unittest discover -s tests
python runtime_check.py
python -m gfs_core --lat 45.0355 --lon 38.9753 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
```

## Опции deploy

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

`--skip-webhooks` нужен только для диагностики/подготовки reverse proxy. Обычный production deploy должен регистрировать webhook автоматически.

## Совместимый переключатель

`install_messenger_runtime.sh` сохранён для аварийного управления уже установленным сервером:

```bash
sudo bash install_messenger_runtime.sh --status
sudo bash install_messenger_runtime.sh --disable   # Telegram-only fallback
sudo bash install_messenger_runtime.sh --enable
```

Новая установка и обычный deploy больше не требуют отдельного запуска этого helper.
