# Deploy Telegram-бота

Бот работает из `/opt/gfs_profile`. Ручное обновление:

```bash
cd ~/gfs_profile
git checkout telegram-bot
git pull --ff-only
sudo bash deploy_telegram_bot.sh --yes
```

## Автоматическое обновление

Рекомендуемый режим — `systemd timer`, который проверяет `origin/telegram-bot`, применяет только fast-forward и запускает штатный deploy:

```bash
cd ~/gfs_profile
sudo bash install_auto_update.sh --yes
```

Проверка:

```bash
sudo bash install_auto_update.sh --status
systemctl status gfs-profile-bot-auto-update.timer
sudo journalctl -u gfs-profile-bot-auto-update.service -n 100 --no-pager
```

При провале нового commit updater возвращает checkout на предыдущий SHA, повторно разворачивает старую версию и помещает неудачный SHA в quarantine. Новый SHA проверяется сразу; тот же неудачный SHA автоматически повторяется через 30 минут. Подробно: [`docs/AUTO_UPDATE.md`](docs/AUTO_UPDATE.md).

## Этапы deploy

Deploy печатает каждый этап, проверяет checksum-синхронизацию checkout с `/opt`, обновляет зависимости, запускает unit tests и runtime preflight, затем выполняет `systemctl restart` и проверяет изменение PID.

По умолчанию lock хранится в:

```text
/run/lock/gfs-profile-bot.deploy.lock
```

Auto-update использует отдельный lock:

```text
/run/lock/gfs-profile-bot-auto-update.lock
```

Перед изменением checkout updater также проверяет штатный deploy-lock. Поэтому ручной и автоматический deploy не выполняются одновременно.

## Запуск и проверка

```bash
sudo bash deploy_telegram_bot.sh --yes
sudo bash deploy_telegram_bot.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

Запуск deploy без root завершается понятным сообщением, поскольку скрипт изменяет `/opt` и systemd.

## Сохраняемые данные

Deploy не удаляет `.env`, `.install-state`, `.venv/`, `.cache_gfs/` и `data/basemap/`. Admin DB сохраняется до `rsync --delete` и восстанавливается после копирования.

## Опции deploy

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

## Git hooks

`install_git_hooks.sh` — только ускоритель после ручного `git pull`/`rebase`. Он не мониторит GitHub. Для автоматического обнаружения новых commit используйте `install_auto_update.sh`.

## DOCX/PDF метеограммы

Python-зависимость: `python-docx>=1.1,<2`. Для PDF на Debian/Astra требуется LibreOffice Writer и шрифт Liberation Sans:

```bash
sudo apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation
```

Проверка:

```bash
python -c "import docx; print(docx.__version__)"
command -v soffice
python meteogram_report_smoke.py
```

Переменные: `LIBREOFFICE_BIN` — необязательный путь к `soffice`; `METEOGRAM_PDF_TIMEOUT` — таймаут конвертации, по умолчанию 90 секунд; `METEOGRAM_REPORT_FONT` — шрифт DOCX, по умолчанию Liberation Sans. Если LibreOffice отсутствует, DOCX остаётся доступным, а запрос PDF автоматически возвращает DOCX с предупреждением.

## Сохранность расписаний

Автоматические отправки хранятся в `.cache_gfs/telegram_schedules.json`. Штатный deploy уже сохраняет `.cache_gfs/`, поэтому переносить отдельную БД или сервис расписаний не требуется. После deploy достаточно обычной проверки `systemctl status`; фоновый планировщик запускается вместе с Telegram long polling.
