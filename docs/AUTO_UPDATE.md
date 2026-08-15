# Автообновление Telegram-бота

## Рекомендуемый режим: systemd timer

Основной механизм — сервер сам проверяет `origin/telegram-bot` через `git fetch`, приводит deployment-checkout точно к удалённой рабочей ветке и запускает deploy только если `/opt/gfs_profile` ещё не соответствует выбранному commit.

```text
GitHub origin/telegram-bot
        ↓ git fetch
сохранение локальных отклонений (backup ref / stash)
        ↓
authoritative reset checkout → origin/telegram-bot
        ↓
deploy_telegram_bot.sh --yes
        ↓
unit tests + runtime preflight
        ↓
systemctl restart
```

Входящий порт, webhook, GitHub token и passwordless sudo не нужны. `systemd` service работает от root, а git-команды выполняются от владельца checkout.

Для серверного checkout ветка `origin/telegram-bot` является источником истины. Updater **не выполняет merge локальной истории с GitHub**: при divergence, rebase или force-push локальная ветка сохраняется как аварийный ref и затем принудительно синхронизируется с remote. Это устраняет остановки `non-fast-forward` и неоднозначное автоматическое разрешение merge-конфликтов.

Установка один раз:

```bash
cd ~/gfs_profile
git checkout telegram-bot
git pull --ff-only
sudo bash install_auto_update.sh --yes
```

Если checkout уже разошёлся с GitHub и старый updater пишет `нельзя применить fast-forward`, один раз синхронизируйте его вручную после появления исправления в remote:

```bash
cd ~/gfs_profile
git fetch origin

git branch "rescue/before-auto-sync-$(date -u +%Y%m%dT%H%M%SZ)" HEAD
git stash push --include-untracked -m "before authoritative auto-update sync" || true

git checkout -f -B telegram-bot origin/telegram-bot
git reset --hard origin/telegram-bot
git clean -fd

sudo bash auto_update_telegram_bot.sh --force
```

`git clean -fd` не удаляет ignored-каталоги (`.venv/`, `.cache_gfs/`, `data/basemap/`). Рабочие `.env`, `.venv` и `.cache_gfs` установленного бота находятся в `/opt/gfs_profile` и штатным deploy не удаляются.

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

## Гарантии и поведение

Updater:

1. всегда получает заданную рабочую ветку `origin/telegram-bot` и считает её канонической для deployment-checkout;
2. локальный commit, который не является предком remote, перед reset сохраняет в `refs/auto-update/backups/...`;
3. незакоммиченные tracked и untracked изменения сохраняет через `git stash --include-untracked`; если stash невозможен (например, незавершённый конфликт), пишет rescue-копию patch/untracked-файлов в `/var/lib/gfs-profile-bot/local-backups/`;
4. после сохранения локальных отклонений делает принудительный `checkout/reset` ровно на remote SHA; `non-fast-forward`, локальный commit, detached/wrong branch и dirty checkout больше не являются штатной причиной остановки;
5. не пытается автоматически «угадывать» разрешение merge-конфликтов — серверная ветка просто заменяется канонической версией GitHub;
6. использует отдельный `flock`, поэтому два обновления одновременно не выполняются;
7. отключает локальные git hooks на время собственного checkout/reset, чтобы не получить рекурсивный deploy;
8. вызывает штатный `deploy_telegram_bot.sh`, поэтому в `/opt/gfs_profile` сохраняются `.env`, `.venv`, `.cache_gfs`, admin DB и basemap;
9. при ошибке deploy возвращает checkout на предыдущий установленный commit и повторно разворачивает старую версию;
10. неудачный SHA записывается как `blocked_rev` и не повторяется каждую минуту;
11. тот же SHA автоматически повторяется после quarantine (по умолчанию 30 минут), поэтому временный внешний сбой не блокирует обновления навсегда;
12. новый remote SHA снимает quarantine немедленно;
13. если пользователь вручную обновил checkout, но `/opt/gfs_profile` остался на старой версии, updater обнаруживает рассогласование `checkout ↔ .install-state` и выполняет deploy.

Сетевой сбой `git fetch` не меняет checkout и не трогает запущенный бот. Проверка повторяется на следующем timer tick.

Важно: «обновляться всегда» означает, что локальный deployment-checkout всегда сходится к доступному `origin/telegram-bot`. Физические ошибки диска, отсутствие git-доступа, занятый deploy lock или неуспешный deploy остаются контролируемыми ошибками; при неуспешном deploy рабочая версия откатывается, а плохой SHA помещается в quarantine.

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
last_check_at=2026-08-15T10:30:00Z
result=deployed
branch=telegram-bot
local_rev=<sha>
remote_rev=<sha>
deployed_rev=<sha>
blocked_rev=
blocked_at_epoch=
sync_mode=authoritative-reset
backup_ref=refs/auto-update/backups/20260815T103000Z-<sha>
stash_rev=<sha или пусто>
backup_dir=
message=Remote синхронизирован и deploy завершён
```

`sync_mode`:

- `already-synced` — checkout уже точно соответствует remote;
- `fast-forward-reset` — remote продолжает локальную историю, но применяется детерминированный reset вместо merge;
- `authoritative-reset` — истории разошлись / локальная ветка опережала remote; локальный HEAD сохранён в backup ref и checkout заменён remote;
- `dirty-reset` — HEAD совпадал с remote, но локальные изменения были сохранены и рабочее дерево очищено;
- `branch-reset` — checkout находился не на целевой ветке и был переключён на `telegram-bot`.

Возможные `result`:

- `up-to-date` — checkout и `/opt` актуальны;
- `deployed` — remote SHA успешно установлен;
- `rolled-back` — новый commit не прошёл deploy, восстановлена предыдущая установленная версия;
- `blocked-revision` — SHA находится в quarantine до автоматического повтора или нового commit;
- `deploy-busy` — выполняется другой ручной/автоматический deploy, checkout не изменён;
- `fetch-error` — временно недоступен remote;
- `sync-error` — checkout технически не удалось привести к remote;
- `rollback-unavailable` — deploy не прошёл, а предыдущий установленный commit отсутствует в локальном object store;
- `rollback-git-failed` / `rollback-deploy-failed` — аварийная ошибка возврата рабочей версии.

Посмотреть сохранённые локальные commits и stashes:

```bash
git for-each-ref --sort=-creatordate --format='%(refname) %(objectname:short)' refs/auto-update/backups/
git stash list
```

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
→ authoritative sync origin/telegram-bot
→ deploy_telegram_bot.sh
```

Плюсы: почти мгновенный deploy только после зелёного CI. Минусы: требуется внешний SSH-доступ, secrets и доверие GitHub-hosted runner. Для закрытого контура этот вариант хуже systemd polling.

## Вариант 3: локальный git hook

`install_git_hooks.sh` ускоряет deploy после **ручного** `git pull` или `rebase`:

```bash
bash install_git_hooks.sh --yes
```

Hook не умеет узнать о commit, который появился на GitHub сам по себе. Поэтому это дополнение, а не замена systemd timer. Если non-interactive sudo недоступен, hook не ломает ручную git-команду: deploy будет выполнен timer-ом по обнаруженному рассогласованию checkout и `/opt`.

## Вариант 4: cron

Технически можно запускать `auto_update_telegram_bot.sh` из root cron. Это рабочий fallback для систем без systemd, но для текущего проекта хуже timer-а: менее удобный статус, журналы, блокировки запуска и управление зависимостями unit-ов.

Рекомендуемая эксплуатационная схема проекта: **systemd timer + authoritative sync рабочей ветки + штатный deploy + git hook только как ускоритель ручных операций**.
