# 🔄 Установка и deploy Telegram-бота

Бот запускается из `/opt/gfs_profile`. Обычный `git pull` обновляет только checkout, поэтому после него нужен `deploy_telegram_bot.sh`.

Основные эксплуатационные скрипты:

```text
install_telegram_bot.sh   первичная установка
deploy_telegram_bot.sh    обновление checkout → /opt и перезапуск
```

## Первичная установка

```bash
bash install_telegram_bot.sh
```

Интерактивный установщик запросит:

```text
TELEGRAM_BOT_TOKEN
DADATA_API_KEY
```

DaData Suggestions требует только API-ключ. Secret Key не нужен.

Неинтерактивно:

```bash
TELEGRAM_BOT_TOKEN='...' \
DADATA_API_KEY='...' \
GEOCODER_PROVIDERS='dadata,local,nominatim' \
bash install_telegram_bot.sh --yes
```

Установщик:

1. ставит системные пакеты;
2. создаёт пользователя `gfsbot`;
3. синхронизирует проект в `/opt/gfs_profile`;
4. создаёт `.venv`;
5. создаёт или дополняет `.env`;
6. проверяет runtime-модули;
7. выполняет контрольный запрос DaData `Москва`;
8. готовит офлайн-подложку;
9. создаёт и запускает systemd-сервис;
10. регистрирует Telegram-команды.

## Обновление после pull

```bash
git checkout telegram-bot
git pull
bash deploy_telegram_bot.sh
```

Deploy сохраняет существующий `/opt/gfs_profile/.env`. Если установка старая и `DADATA_API_KEY` отсутствует, скрипт запросит ключ и запишет его до runtime-проверки и перезапуска.

Неинтерактивно:

```bash
DADATA_API_KEY='...' bash deploy_telegram_bot.sh --yes
```

При `--yes` отсутствие обязательного ключа является ошибкой. Сервис не перезапускается с неполной конфигурацией.

## Deploy lock

Deploy защищён `flock`, чтобы два обновления не выполнялись одновременно.

Ошибка вида:

```text
deploy_telegram_bot.sh: line ...: /tmp/gfs-profile-bot.deploy.lock: Permission denied
```

возникала до вызова `flock`: shell не мог открыть предсказуемый глобальный путь в `/tmp`. Причиной мог быть ранее созданный объект с другими правами, каталог или ссылка, ACL/атрибуты либо ограничения конкретной конфигурации `/tmp`.

Lock больше не создаётся по пути `/tmp/gfs-profile-bot.deploy.lock`.

Новый порядок выбора:

```text
DEPLOY_LOCK_PATH, если задан явно
→ git-dir текущего checkout, обычно .git/gfs-profile-bot.deploy.lock
→ XDG_RUNTIME_DIR
→ ~/.cache/gfs-profile/gfs-profile-bot.deploy.lock
```

Нормальный вывод:

```text
✓ Deploy lock: /home/user/gfs_profile/.git/gfs-profile-bot.deploy.lock
```

Старый объект в `/tmp` новый deploy игнорирует. Удалять его необязательно. Для очистки сначала проверьте тип объекта:

```bash
sudo ls -ld /tmp/gfs-profile-bot.deploy.lock
sudo rm -f /tmp/gfs-profile-bot.deploy.lock   # только если это обычный файл или ссылка
```

Явное переопределение требуется только для нестандартного окружения:

```bash
DEPLOY_LOCK_PATH="$HOME/.cache/gfs-profile/custom.deploy.lock" \
bash deploy_telegram_bot.sh --yes
```

## Проверка DaData

Deploy до перезапуска выполняет:

```bash
cd /opt/gfs_profile
set -a
source .env
set +a
.venv/bin/python geocoder_preflight.py
```

Ожидается:

```text
Geocoder providers: dadata,local,nominatim
DaData OK: Москва -> 55...., 37....
```

Типовые причины отказа:

- ключ отсутствует;
- ключ неверный;
- почта DaData не подтверждена;
- Suggestions отключены для ключа;
- исчерпан дневной лимит;
- превышена частота запросов.

Подробности: [`docs/DADATA_GEOCODER.md`](docs/DADATA_GEOCODER.md).

## Опции deploy

```text
--install-dir DIR
--service-name NAME
--service-user USER
--python PATH
--yes
--install-system-packages
--skip-pip
--skip-commands
--no-restart
--status
```

## Состояние

```bash
bash deploy_telegram_bot.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

`--status` показывает:

- checkout и revision;
- каталог `/opt`;
- наличие `.env` и `.venv`;
- `GEOCODER_PROVIDERS`;
- маскированный `DADATA_API_KEY`;
- admin DB;
- состояние systemd.

## Проверка проекта

```bash
bash -n install_telegram_bot.sh
bash -n deploy_telegram_bot.sh
python -m unittest discover -s tests
python runtime_check.py
python -m gfs_core --lat 45.0355 --lon 38.9753 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
```

## Что сохраняется

Deploy не удаляет:

```text
/opt/gfs_profile/.env
/opt/gfs_profile/.install-state
/opt/gfs_profile/.venv/
/opt/gfs_profile/.cache_gfs/
/opt/gfs_profile/data/basemap/
```

Перед `rsync --delete` отдельно сохраняется custom/default SQLite admin DB, после синхронизации она восстанавливается при необходимости.

## Переменные геокодера

```text
GEOCODER_PROVIDERS=dadata,local,nominatim
DADATA_API_KEY=
DADATA_SUGGEST_URL=https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address
DADATA_TIMEOUT=12
GEOCODE_CACHE_DIR=.cache_gfs/geocode
GEOCODE_CACHE_TTL_SECONDS=2592000
NOMINATIM_URL=https://nominatim.openstreetmap.org/search
GEOCODER_USER_AGENT=gfs-profile-telegram-bot/0.1
```

Отключить Nominatim:

```text
GEOCODER_PROVIDERS=dadata,local
```
