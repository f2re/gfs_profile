# Автообновление Telegram-бота

## Рекомендуемый режим: systemd timer

Основной механизм — сервер сам проверяет `origin/telegram-bot` через `git fetch` и запускает deploy только при появлении нового commit.

```text
GitHub origin/telegram-bot
        ↓ git fetch
clean checkout + fast-forward only
        ↓
deploy_telegram_bot.sh --yes
        ↓
unit tests + runtime preflight
        ↓
systemctl restart
```

Входящий порт, webhook, GitHub token и passwordless sudo не нужны. `systemd` service работает от root, а git-команды выполняются от владельца checkout.

Установка один раз:

```bash
cd ~/gfs_profile
git checkout telegram-bot
git pull --ff-only
sudo bash install_auto_update.sh --yes
```

Установщик до создания timer выполняет `git ls-remote` от имени владельца checkout. Это проверяет доступ именно в неинтерактивной systemd-среде. Если `origin` использует SSH-ключ, доступный только через `ssh-agent`, настройте постоянный deploy key либо fetch по HTTPS. Для публичного репозитория наиболее простой fetch URL:

```bash
git remote set-url origin https://github.com/f2re/gfs_profile.git
```

Если с сервера также выполняется push по SSH, можно отдельно сохранить push URL:

```bash
git remote set-url --push origin git@github.com:f2re/gfs_profile.git
```

По умолчанию проверка выполняется примерно раз в 60 секунд. Другой интервал:

```bash
sudo bash install_auto_update.sh --interval 120 --yes
```

Минимальный разрешённый интервал — 15 секунд. Для обычной эксплуатации рекомендуются 60–120 секунд.

## Гарантии безопасности

Updater:

1. работает только с заданной веткой `telegram-bot`;
2. отказывается работать с dirty checkout;
3. выполняет только `fast-forward`, force reset на remote запрещён;
4. использует отдельный `flock`, поэтому два обновления одновременно не выполняются;
5. отключает локальные git hooks на время собственного merge/reset, чтобы не получить рекурсивный deploy;
6. вызывает штатный `deploy_telegram_bot.sh`, поэтому сохраняются `.env`, `.venv`, `.cache_gfs`, admin DB и basemap;
7. при ошибке deploy возвращает checkout на предыдущий commit и повторно разворачивает старую версию;
8. неудачный SHA записывается как `blocked_rev` и не повторяется каждую минуту;
9. тот же SHA автоматически повторяется после quarantine (по умолчанию 30 минут), поэтому временный внешний сбой не блокирует обновления навсегда;
10. новый remote SHA снимает quarantine немедленно;
11. если пользователь вручную сделал `git pull`, но `/opt/gfs_profile` остался на старой версии, updater обнаруживает расхождение `checkout ↔ .install-state` и выполняет deploy.

Сетевой сбой `git fetch` не меняет checkout и не трогает запущенный бот. Проверка повторяется на следующем timer tick.

## Состояние

```bash
sudo bash install_auto_update.sh --status
systemctl status gfs-profile-bot-auto-update.timer
systemctl status gfs-profile-bot-auto-update.service
systemctl list-timers gfs-profile-bot-auto-update.timer --all
```

Журнал:

```bash
sudo journalctl -u gfs-profile-bot-auto-update.service -n 100 --no-pager
sudo journalctl -u gfs-profile-bot-auto-update.service -f
```

Состояние updater хранится в:

```text
/var/lib/gfs-profile-bot/auto-update.state
```

Пример:

```text
last_check_at=2026-08-07T02:30:00Z
result=deployed
branch=telegram-bot
local_rev=<sha>
remote_rev=<sha>
deployed_rev=<sha>
blocked_rev=
blocked_at_epoch=
message=Обновление и deploy завершены
```

Возможные `result`:

- `up-to-date` — checkout и `/opt` актуальны;
- `deployed` — новый commit успешно установлен;
- `rolled-back` — новый commit не прошёл deploy, восстановлена предыдущая версия;
- `blocked-revision` — SHA находится в quarantine до автоматического повтора или нового commit;
- `deploy-busy` — выполняется другой ручной/автоматический deploy, checkout не изменён;
- `fetch-error` — временно недоступен remote;
- `dirty-checkout` — есть локальные изменения;
- `wrong-branch` — checkout не в `telegram-bot`;
- `non-fast-forward` — локальная и удалённая история разошлись.

Повторить заблокированный SHA вручную после устранения внешней причины:

```bash
sudo systemctl stop gfs-profile-bot-auto-update.timer
sudo AUTO_UPDATE_REPO_ROOT=$HOME/gfs_profile \
  bash ~/gfs_profile/auto_update_telegram_bot.sh --force
sudo systemctl start gfs-profile-bot-auto-update.timer
```

Обычно `--force` не нужен: правильнее добавить исправляющий commit в `telegram-bot`.

## Отключение

```bash
sudo bash install_auto_update.sh --disable
```

Основной бот при этом не удаляется и продолжает работать.

## Вариант 2: GitHub Actions → SSH

Подходит, если сервер имеет стабильный SSH-доступ с GitHub Actions и допустимо хранить deploy key/host key в GitHub Secrets.

Схема:

```text
push telegram-bot
→ GitHub Actions tests
→ SSH на сервер
→ git fetch + ff-only
→ deploy_telegram_bot.sh
```

Плюсы: почти мгновенный deploy только после зелёного CI. Минусы: требуется внешний SSH-доступ, secrets и доверие GitHub-hosted runner. Для закрытого контура этот вариант хуже systemd polling.

## Вариант 3: локальный git hook

`install_git_hooks.sh` ускоряет deploy после **ручного** `git pull` или `rebase`:

```bash
bash install_git_hooks.sh --yes
```

Hook не умеет узнать о commit, который появился на GitHub сам по себе. Поэтому это дополнение, а не замена systemd timer. Если non-interactive sudo недоступен, hook не ломает `git pull`: deploy будет выполнен timer-ом по обнаруженному рассогласованию checkout и `/opt`.

## Вариант 4: cron

Технически можно запускать `auto_update_telegram_bot.sh` из root cron. Это рабочий fallback для систем без systemd, но для текущего проекта хуже timer-а: менее удобный статус, журналы, блокировки запуска и управление зависимостями unit-ов.

Рекомендуемая эксплуатационная схема проекта: **systemd timer + штатный deploy + git hook только как ускоритель ручного pull**.
