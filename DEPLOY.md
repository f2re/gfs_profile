# 🔄 Обновление и автообновление Telegram-бота

Проблема: `git pull` обновляет только рабочий git-каталог, но установленный бот работает из `/opt/gfs_profile`. Поэтому после pull нужно синхронизировать код в `/opt`, обновить зависимости и перезапустить systemd-сервис.

Для этого добавлены два скрипта:

```text
install_telegram_bot.sh   первичная установка
install_git_hooks.sh      установка локальных git hooks для автообновления после git pull
deploy_telegram_bot.sh    синхронизация git checkout → /opt + pip install + restart
```

## ✅ Рекомендуемая схема

Первичная установка:

```bash
bash install_telegram_bot.sh
```

После первичной установки включить автообновление из этого же git checkout:

```bash
bash install_git_hooks.sh
```

После этого обычный pull будет автоматически раскатывать изменения в `/opt/gfs_profile` и перезапускать сервис:

```bash
git pull
```

Что выполнит hook:

1. Возьмёт текущий checkout после `git pull`.
2. Вызовет `deploy_telegram_bot.sh --yes`.
3. Скопирует код в `/opt/gfs_profile` через `rsync`.
4. Сохранит `.env`, `.install-state`, `.venv`, кэш и локальные служебные файлы.
5. Выполнит `pip install -r /opt/gfs_profile/requirements.txt`.
6. Проверит офлайн-подложку Natural Earth и скачает недостающие слои, если кэш не готов.
7. Выполнит `systemctl restart gfs-profile-bot.service`.
8. Запишет лог в `.git/gfs-profile-deploy.log`.

## 🛠️ Ручной deploy после pull

Если hooks не нужны или sudo требует пароль:

```bash
git pull
bash deploy_telegram_bot.sh
```

Неразговорный режим:

```bash
bash deploy_telegram_bot.sh --yes
```

Только проверить состояние:

```bash
bash deploy_telegram_bot.sh --status
```

Без перезапуска сервиса:

```bash
bash deploy_telegram_bot.sh --no-restart
```

Без обновления pip-зависимостей:

```bash
bash deploy_telegram_bot.sh --skip-pip
```

## ⚙️ Опции deploy

```text
--install-dir DIR       каталог установки, по умолчанию /opt/gfs_profile
--service-name NAME     имя systemd-сервиса, по умолчанию gfs-profile-bot
--service-user USER     системный пользователь, по умолчанию gfsbot
--python PATH           Python для создания venv, если он отсутствует
--yes                   не задавать вопросов
--skip-pip              не обновлять Python-зависимости
--no-restart            не перезапускать сервис
--status                только показать состояние
```

## 🔐 Важное про sudo

Git hooks выполняются от пользователя, который запускает `git pull`. Для полностью автоматического режима этому пользователю нужен доступ к командам, которые требуют root-прав: `rsync` в `/opt`, `chown`, `systemctl restart`, иногда `pip install` в venv пользователя `gfsbot`.

Если sudo требует пароль, hook может остановить `git pull` или завершиться ошибкой. В этом случае используйте ручной deploy:

```bash
git pull
bash deploy_telegram_bot.sh
```

Или настройте ограниченный passwordless sudo только для нужных команд.

## 🧪 Проверка после обновления

```bash
bash deploy_telegram_bot.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 80 --no-pager
```

Проверка ядра без Telegram:

```bash
sudo -u gfsbot /opt/gfs_profile/.venv/bin/python -m gfs_core --lat 55.75 --lon 37.62 --lead 24
```

Проверка офлайн-подложки карт:

```bash
sudo -u gfsbot /opt/gfs_profile/.venv/bin/python /opt/gfs_profile/prepare_basemap_cache.py --check
sudo -u gfsbot /opt/gfs_profile/.venv/bin/python /opt/gfs_profile/prepare_basemap_cache.py --resolution 10m
```

Проверка в Telegram:

```text
/status
Москва +24
```

## 📁 Что сохраняется при deploy

Deploy не удаляет:

```text
/opt/gfs_profile/.env
/opt/gfs_profile/.install-state
/opt/gfs_profile/.venv/
/opt/gfs_profile/.cache_gfs/
/opt/gfs_profile/data/basemap/
```

Это важно: токен Telegram, состояние установки, виртуальное окружение, GRIB-кэш и офлайн-векторная подложка не теряются при обновлении.

## 🧯 Где смотреть ошибки hook

```bash
cat .git/gfs-profile-deploy.log
tail -f .git/gfs-profile-deploy.log
```

Если сервис не стартует:

```bash
sudo journalctl -u gfs-profile-bot.service -n 120 --no-pager
```
