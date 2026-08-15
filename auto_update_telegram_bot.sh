#!/usr/bin/env bash
# Надёжное автообновление GFS Profile Bot из рабочей ветки.
# origin/<branch> является источником истины для deployment-checkout.
# Предназначен для запуска root-oneshot service по systemd timer.

set -Eeuo pipefail

DEFAULT_BRANCH="telegram-bot"
DEFAULT_REMOTE="origin"
DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_STATE_DIR="/var/lib/gfs-profile-bot"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${AUTO_UPDATE_REPO_ROOT:-$SCRIPT_DIR}"
BRANCH="${AUTO_UPDATE_BRANCH:-$DEFAULT_BRANCH}"
REMOTE="${AUTO_UPDATE_REMOTE:-$DEFAULT_REMOTE}"
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
STATE_DIR="${AUTO_UPDATE_STATE_DIR:-$DEFAULT_STATE_DIR}"
STATE_FILE="${AUTO_UPDATE_STATE_FILE:-$STATE_DIR/auto-update.state}"
LOCK_FILE="${AUTO_UPDATE_LOCK_FILE:-/run/lock/${SERVICE_NAME}-auto-update.lock}"
DEPLOY_LOCK_FILE="${AUTO_UPDATE_DEPLOY_LOCK_FILE:-/run/lock/${SERVICE_NAME}.deploy.lock}"
INNER_DEPLOY_LOCK_FILE="${AUTO_UPDATE_INNER_DEPLOY_LOCK_FILE:-/run/lock/${SERVICE_NAME}-auto-update-inner.deploy.lock}"
DEPLOY_SCRIPT="${AUTO_UPDATE_DEPLOY_SCRIPT:-$REPO_ROOT/deploy_telegram_bot.sh}"
BLOCK_RETRY_SECONDS="${AUTO_UPDATE_BLOCK_RETRY_SECONDS:-1800}"
BACKUP_REF_PREFIX="${AUTO_UPDATE_BACKUP_REF_PREFIX:-refs/auto-update/backups}"
blocked_at_epoch=""
backup_ref=""
stash_rev=""
backup_dir=""
sync_mode=""
FORCE_RETRY=0
STATUS_ONLY=0

log() { printf '%s\n' "[auto-update] $*" >&2; }
warn() { printf '%s\n' "[auto-update] WARNING: $*" >&2; }
fail() { log "ERROR: $*"; return 1; }
now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
backup_stamp() { date -u +%Y%m%dT%H%M%SZ; }

usage() {
  cat <<EOF2
Автообновление GFS Profile Bot

Использование:
  sudo bash auto_update_telegram_bot.sh [--status] [--force]

Опции:
  --status   показать сохранённое состояние без git fetch/deploy
  --force    повторить ранее заблокированный commit
  -h, --help

Переменные:
  AUTO_UPDATE_REPO_ROOT   git checkout (по умолчанию каталог скрипта)
  AUTO_UPDATE_BRANCH      ветка (по умолчанию telegram-bot)
  AUTO_UPDATE_REMOTE      remote (по умолчанию origin)
  AUTO_UPDATE_STATE_DIR   каталог состояния
  AUTO_UPDATE_LOCK_FILE   lock-файл updater
  AUTO_UPDATE_DEPLOY_LOCK_FILE  общий lock штатного deploy
  AUTO_UPDATE_INNER_DEPLOY_LOCK_FILE  внутренний lock дочернего deploy
  AUTO_UPDATE_BLOCK_RETRY_SECONDS  повтор плохого SHA, по умолчанию 1800
  AUTO_UPDATE_BACKUP_REF_PREFIX  namespace локальных backup refs
  AUTO_UPDATE_DEPLOY_SCRIPT  deploy-скрипт (для тестов/аварийной настройки)
EOF2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status) STATUS_ONLY=1; shift ;;
    --force) FORCE_RETRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Неизвестная опция: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! [[ "$BLOCK_RETRY_SECONDS" =~ ^[0-9]+$ ]]; then
  log "Некорректный AUTO_UPDATE_BLOCK_RETRY_SECONDS=$BLOCK_RETRY_SECONDS; использую 1800"
  BLOCK_RETRY_SECONDS=1800
fi

state_value() {
  local key="$1"
  [[ -f "$STATE_FILE" ]] || return 0
  sed -n "s/^${key}=//p" "$STATE_FILE" | tail -n 1
}

write_state() {
  local result="$1" local_rev="${2:-}" remote_rev="${3:-}" deployed_rev="${4:-}" blocked_rev="${5:-}" message="${6:-}"
  mkdir -p "$STATE_DIR"
  local tmp
  tmp="$(mktemp "$STATE_DIR/.auto-update.XXXXXX")"
  {
    printf 'last_check_at=%s\n' "$(now_utc)"
    printf 'result=%s\n' "$result"
    printf 'branch=%s\n' "$BRANCH"
    printf 'local_rev=%s\n' "$local_rev"
    printf 'remote_rev=%s\n' "$remote_rev"
    printf 'deployed_rev=%s\n' "$deployed_rev"
    printf 'blocked_rev=%s\n' "$blocked_rev"
    printf 'blocked_at_epoch=%s\n' "$blocked_at_epoch"
    printf 'sync_mode=%s\n' "$sync_mode"
    printf 'backup_ref=%s\n' "$backup_ref"
    printf 'stash_rev=%s\n' "$stash_rev"
    printf 'backup_dir=%s\n' "$backup_dir"
    printf 'message=%s\n' "$(printf '%s' "$message" | tr '\n\r' '  ')"
  } >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$STATE_FILE"
}

print_status() {
  echo "repo=$REPO_ROOT"
  echo "branch=$BRANCH"
  echo "remote=$REMOTE"
  echo "state=$STATE_FILE"
  if [[ -f "$STATE_FILE" ]]; then
    cat "$STATE_FILE"
  else
    echo "result=never-run"
  fi
}

if [[ "$STATUS_ONLY" -eq 1 ]]; then
  print_status
  exit 0
fi

[[ -d "$REPO_ROOT/.git" ]] || { fail "$REPO_ROOT не является git checkout"; exit 1; }
[[ -f "$DEPLOY_SCRIPT" ]] || { fail "Не найден deploy script: $DEPLOY_SCRIPT"; exit 1; }
command -v git >/dev/null 2>&1 || { fail "Не найден git"; exit 1; }
command -v flock >/dev/null 2>&1 || { fail "Не найден flock"; exit 1; }

mkdir -p "$(dirname "$LOCK_FILE")" "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Другой auto-update уже выполняется"
  exit 0
fi

REPO_USER="${AUTO_UPDATE_REPO_USER:-$(stat -c '%U' "$REPO_ROOT/.git" 2>/dev/null || id -un)}"
REPO_HOME="$(getent passwd "$REPO_USER" 2>/dev/null | cut -d: -f6 || true)"
REPO_HOME="${REPO_HOME:-$HOME}"

run_git() {
  if [[ "$(id -u)" -eq 0 && "$REPO_USER" != "root" ]]; then
    runuser -u "$REPO_USER" -- env HOME="$REPO_HOME" git -C "$REPO_ROOT" "$@"
  else
    git -C "$REPO_ROOT" "$@"
  fi
}

resolve_installed_rev() {
  local raw="" resolved=""
  if [[ -f "$INSTALL_DIR/.install-state" ]]; then
    raw="$(sed -n 's/^source_rev=//p' "$INSTALL_DIR/.install-state" | tail -n 1)"
  fi
  [[ -n "$raw" ]] || return 0
  resolved="$(run_git rev-parse "${raw}^{commit}" 2>/dev/null || true)"
  printf '%s' "$resolved"
}

revision_matches() {
  local full="$1" known="$2"
  [[ -n "$full" && -n "$known" ]] || return 1
  [[ "$full" == "$known" || "$full" == "$known"* || "$known" == "$full"* ]]
}

save_fallback_dirty_backup() {
  local stamp="$1" untracked_list
  backup_dir="$STATE_DIR/local-backups/$stamp"
  mkdir -p "$backup_dir"
  run_git diff >"$backup_dir/worktree.patch" 2>/dev/null || true
  run_git diff --cached >"$backup_dir/index.patch" 2>/dev/null || true
  untracked_list="$backup_dir/untracked.list"
  run_git ls-files --others --exclude-standard -z >"$untracked_list" 2>/dev/null || true
  if [[ -s "$untracked_list" ]]; then
    tar -C "$REPO_ROOT" --null -T "$untracked_list" -czf "$backup_dir/untracked.tar.gz" 2>/dev/null || true
  fi
  warn "git stash не сработал; rescue-копия локальных изменений: $backup_dir"
}

preserve_local_checkout() {
  local local_rev="$1" remote_rev="$2" current_branch="$3" dirty="$4" stamp stash_message
  stamp="$(backup_stamp)"

  if [[ -n "$local_rev" ]] && ! run_git merge-base --is-ancestor "$local_rev" "$remote_rev" >/dev/null 2>&1; then
    backup_ref="${BACKUP_REF_PREFIX}/${stamp}-${local_rev:0:12}"
    if run_git update-ref "$backup_ref" "$local_rev"; then
      log "Сохранил локальную историю: $backup_ref -> ${local_rev:0:12}"
    else
      backup_ref=""
      warn "Не удалось создать backup ref для ${local_rev:0:12}"
    fi
  fi

  if [[ -n "$dirty" ]]; then
    stash_message="auto-update backup ${stamp} branch=${current_branch:-detached} rev=${local_rev:0:12}"
    if run_git stash push --include-untracked -m "$stash_message" >/dev/null 2>&1; then
      stash_rev="$(run_git rev-parse refs/stash 2>/dev/null || true)"
      log "Сохранил локальные изменения в stash ${stash_rev:0:12}"
    else
      save_fallback_dirty_backup "$stamp"
    fi
  fi
}

force_sync_checkout() {
  local remote_rev="$1"
  # Deployment-checkout однонаправленный: удалённая рабочая ветка является
  # источником истины. Merge здесь не нужен и только создаёт конфликтные состояния.
  run_git -c core.hooksPath=/dev/null checkout -f -B "$BRANCH" "$remote_rev" >/dev/null
  run_git -c core.hooksPath=/dev/null reset --hard "$remote_rev" >/dev/null
  run_git clean -fd >/dev/null

  local actual_branch actual_rev dirty
  actual_branch="$(run_git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  actual_rev="$(run_git rev-parse HEAD 2>/dev/null || true)"
  dirty="$(run_git status --porcelain --untracked-files=normal)"
  [[ "$actual_branch" == "$BRANCH" && "$actual_rev" == "$remote_rev" && -z "$dirty" ]]
}

current_branch="$(run_git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
local_rev="$(run_git rev-parse HEAD 2>/dev/null || true)"
deployed_rev="$(state_value deployed_rev)"
blocked_rev="$(state_value blocked_rev)"
blocked_at_epoch="$(state_value blocked_at_epoch)"

log "Проверяю $REMOTE/$BRANCH"
if ! run_git fetch --prune "$REMOTE" "+refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH"; then
  write_state "fetch-error" "$local_rev" "" "$deployed_rev" "$blocked_rev" "git fetch завершился ошибкой; повтор будет по таймеру"
  log "Не удалось получить remote; повтор будет по следующему timer tick"
  exit 0
fi

remote_rev="$(run_git rev-parse "refs/remotes/$REMOTE/$BRANCH")"
local_rev="$(run_git rev-parse HEAD)"
installed_rev="$(resolve_installed_rev)"
if [[ -n "$installed_rev" ]]; then
  deployed_rev="$installed_rev"
fi

if [[ "$remote_rev" == "$blocked_rev" && "$FORCE_RETRY" -ne 1 ]]; then
  now_epoch="$(date +%s)"
  if [[ "$blocked_at_epoch" =~ ^[0-9]+$ ]] && (( now_epoch - blocked_at_epoch < BLOCK_RETRY_SECONDS )); then
    retry_in=$(( BLOCK_RETRY_SECONDS - (now_epoch - blocked_at_epoch) ))
    write_state "blocked-revision" "$local_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "Повтор неудачного SHA через ${retry_in}s либо сразу после нового commit"
    log "Commit ${remote_rev:0:12} временно заблокирован; повтор примерно через ${retry_in}s"
    exit 0
  fi
  log "Истёк quarantine для ${remote_rev:0:12}; повторяю deploy автоматически"
fi

mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")"
exec 8>"$DEPLOY_LOCK_FILE"
if ! flock -n 8; then
  write_state "deploy-busy" "$local_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "Другой ручной или автоматический deploy уже выполняется"
  log "Deploy lock занят; checkout не изменён, повтор будет по таймеру"
  exit 0
fi

pre_sync_rev="$local_rev"
pre_sync_branch="$current_branch"
pre_sync_dirty="$(run_git status --porcelain --untracked-files=normal)"
rollback_rev="$pre_sync_rev"
if [[ -n "$deployed_rev" ]] && run_git cat-file -e "${deployed_rev}^{commit}" 2>/dev/null; then
  rollback_rev="$(run_git rev-parse "${deployed_rev}^{commit}")"
fi

need_sync=0
if [[ "$pre_sync_rev" != "$remote_rev" || "$pre_sync_branch" != "$BRANCH" || -n "$pre_sync_dirty" ]]; then
  need_sync=1
fi

if [[ "$need_sync" -eq 1 ]]; then
  preserve_local_checkout "$pre_sync_rev" "$remote_rev" "$pre_sync_branch" "$pre_sync_dirty"
  if [[ "$pre_sync_rev" != "$remote_rev" ]] && run_git merge-base --is-ancestor "$pre_sync_rev" "$remote_rev" >/dev/null 2>&1; then
    sync_mode="fast-forward-reset"
  elif [[ "$pre_sync_rev" != "$remote_rev" ]]; then
    sync_mode="authoritative-reset"
  elif [[ "$pre_sync_branch" != "$BRANCH" ]]; then
    sync_mode="branch-reset"
  else
    sync_mode="dirty-reset"
  fi
  log "Синхронизирую checkout с $REMOTE/$BRANCH: ${pre_sync_rev:0:12} -> ${remote_rev:0:12} ($sync_mode)"
  if ! force_sync_checkout "$remote_rev"; then
    write_state "sync-error" "$(run_git rev-parse HEAD 2>/dev/null || true)" "$remote_rev" "$deployed_rev" "$blocked_rev" "Не удалось привести checkout точно к remote"
    fail "Не удалось синхронизировать checkout с $REMOTE/$BRANCH"
    exit 1
  fi
else
  sync_mode="already-synced"
fi

candidate_rev="$(run_git rev-parse HEAD)"
if [[ "$candidate_rev" != "$remote_rev" ]]; then
  write_state "revision-mismatch" "$candidate_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "HEAD после синхронизации не совпал с remote"
  fail "HEAD после обновления не совпадает с remote"
  exit 1
fi

if revision_matches "$candidate_rev" "$deployed_rev"; then
  write_state "up-to-date" "$candidate_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "Checkout принудительно синхронизирован с remote; /opt уже соответствует commit"
  log "Уже актуально и развёрнуто: ${candidate_rev:0:12}"
  exit 0
fi

log "Запускаю deploy ${candidate_rev:0:12}"
if DEPLOY_LOCK_PATH="$INNER_DEPLOY_LOCK_FILE" bash "$DEPLOY_SCRIPT" --yes; then
  blocked_rev=""
  blocked_at_epoch=""
  write_state "deployed" "$candidate_rev" "$remote_rev" "$candidate_rev" "" "Remote синхронизирован и deploy завершён"
  log "Готово: ${candidate_rev:0:12}"
  exit 0
else
  deploy_code=$?
fi

if [[ -z "$rollback_rev" ]] || ! run_git cat-file -e "${rollback_rev}^{commit}" 2>/dev/null; then
  blocked_rev="$candidate_rev"
  blocked_at_epoch="$(date +%s)"
  write_state "rollback-unavailable" "$candidate_rev" "$remote_rev" "$deployed_rev" "$candidate_rev" "Deploy не прошёл; предыдущий установленный commit недоступен в checkout"
  fail "Deploy ${candidate_rev:0:12} не прошёл и нет доступного commit для rollback"
  exit "$deploy_code"
fi

log "Deploy ${candidate_rev:0:12} не прошёл; откатываю checkout и /opt на ${rollback_rev:0:12}"
if ! run_git -c core.hooksPath=/dev/null checkout -f -B "$BRANCH" "$rollback_rev" >/dev/null \
  || ! run_git -c core.hooksPath=/dev/null reset --hard "$rollback_rev" >/dev/null; then
  write_state "rollback-git-failed" "$candidate_rev" "$remote_rev" "$deployed_rev" "$candidate_rev" "Не удалось вернуть checkout на предыдущий установленный commit"
  fail "КРИТИЧНО: не удалось откатить git checkout"
  exit "$deploy_code"
fi
run_git clean -fd >/dev/null || true

rollback_ok=0
if DEPLOY_LOCK_PATH="$INNER_DEPLOY_LOCK_FILE" bash "$DEPLOY_SCRIPT" --yes; then
  rollback_ok=1
  deployed_rev="$rollback_rev"
  log "Rollback deploy завершён: ${rollback_rev:0:12}"
else
  log "КРИТИЧНО: rollback deploy тоже завершился ошибкой; checkout возвращён на предыдущий commit"
fi

blocked_rev="$candidate_rev"
blocked_at_epoch="$(date +%s)"
if [[ "$rollback_ok" -eq 1 ]]; then
  write_state "rolled-back" "$rollback_rev" "$remote_rev" "$rollback_rev" "$candidate_rev" "Новый commit провалил deploy; повтор после quarantine или при новом remote SHA"
else
  write_state "rollback-deploy-failed" "$rollback_rev" "$remote_rev" "$deployed_rev" "$candidate_rev" "Checkout откатан, но повторный deploy старой версии завершился ошибкой"
fi
exit "$deploy_code"
