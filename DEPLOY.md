# Deploy Telegram-бота

Бот работает из `/opt/gfs_profile`, поэтому после `git pull` требуется deploy:

```bash
cd ~/gfs_profile
git checkout telegram-bot
git pull
sudo bash deploy_telegram_bot.sh --yes
```

## Исправление преждевременного завершения

Скрипт использует `set -Eeuo pipefail`. Ранее optional-функции содержали конструкции вида:

```bash
[[ "$INSTALL_SYSTEM_PACKAGES" -eq 1 ]] || return
```

При ложном условии `return` без кода возвращал статус `1`. Поэтому deploy завершался сразу после получения lock и не доходил до `rsync` и `systemctl restart`.

Теперь пропускаемые этапы явно выполняют `return 0`. Исправлены системные пакеты, отсутствие admin DB, `--skip-pip`, `--skip-tests`, `--skip-commands`, `--no-restart` и отключённый DaData.

## Этапы

Deploy печатает каждый этап, проверяет checksum-синхронизацию checkout с `/opt`, запускает unit tests и runtime preflight, затем выполняет `systemctl restart` и проверяет изменение PID.

По умолчанию lock хранится в:

```text
/run/lock/gfs-profile-bot.deploy.lock
```

## Запуск и проверка

```bash
sudo bash deploy_telegram_bot.sh --yes
sudo bash deploy_telegram_bot.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

Запуск без root завершается понятным сообщением, поскольку скрипт изменяет `/opt` и systemd.

## Сохраняемые данные

Deploy не удаляет `.env`, `.install-state`, `.venv/`, `.cache_gfs/` и `data/basemap/`. Admin DB сохраняется до `rsync --delete` и восстанавливается после копирования.

## Опции

```text
--yes
--install-system-packages
--skip-pip
--skip-tests
--skip-commands
--no-restart
--status
```

`--skip-commands` оставляет прежнее Telegram-меню. По умолчанию команды регистрируются после успешного перезапуска.
