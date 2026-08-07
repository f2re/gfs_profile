#!/usr/bin/env bash
# Безопасное автообновление GFS Profile Bot из рабочей ветки.
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
blocked_at_epoch=""
FORCE_RETRY=0
STATUS_ONLY=0

log() { printf '%s\n' "[auto-update] $*" >&2; }
fail() { log "ERROR: $*"; return 1; }
now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

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

current_branch="$(run_git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
local_rev="$(run_git rev-parse HEAD 2>/dev/null || true)"
deployed_rev="$(state_value deployed_rev)"
blocked_rev="$(state_value blocked_rev)"
blocked_at_epoch="$(state_value blocked_at_epoch)"

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

if [[ -z "$deployed_rev" ]]; then
  deployed_rev="$(resolve_installed_rev)"
fi

if [[ "$current_branch" != "$BRANCH" ]]; then
  write_state "wrong-branch" "$local_rev" "" "$deployed_rev" "$blocked_rev" "checkout=$current_branch; expected=$BRANCH"
  fail "Checkout находится в ветке '$current_branch', требуется '$BRANCH'"
  exit 1
fi

if [[ -n "$(run_git status --porcelain --untracked-files=normal)" ]]; then
  write_state "dirty-checkout" "$local_rev" "" "$deployed_rev" "$blocked_rev" "Рабочее дерево содержит локальные изменения"
  fail "Рабочее дерево не чистое; автообновление отменено"
  exit 1
fi

log "Проверяю $REMOTE/$BRANCH"
if ! run_git fetch --prune "$REMOTE" "+refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH"; then
  write_state "fetch-error" "$local_rev" "" "$deployed_rev" "$blocked_rev" "git fetch завершился ошибкой; повтор будет по таймеру"
  log "Не удалось получить remote; повтор будет по следующему timer tick"
  exit 0
fi

remote_rev="$(run_git rev-parse "refs/remotes/$REMOTE/$BRANCH")"
local_rev="$(run_git rev-parse HEAD)"

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

old_rev="$local_rev"
candidate_rev="$local_rev"

mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")"
exec 8>"$DEPLOY_LOCK_FILE"
if ! flock -n 8; then
  write_state "deploy-busy" "$local_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "Другой ручной или автоматический deploy уже выполняется"
  log "Deploy lock занят; checkout не изменён, повтор будет по таймеру"
  exit 0
fi

if [[ "$remote_rev" == "$local_rev" ]]; then
  if revision_matches "$local_rev" "$deployed_rev"; then
    write_state "up-to-date" "$local_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "Checkout и /opt соответствуют одному commit"
    log "Уже актуально и развёрнуто: ${local_rev:0:12}"
    exit 0
  fi
  if [[ -n "$deployed_rev" ]] && run_git cat-file -e "${deployed_rev}^{commit}" 2>/dev/null; then
    old_rev="$(run_git rev-parse "${deployed_rev}^{commit}")"
  fi
  log "Checkout уже обновлён до ${local_rev:0:12}, но /opt отстаёт; запускаю deploy"
else
  if ! run_git merge-base --is-ancestor "$local_rev" "$remote_rev"; then
    write_state "non-fast-forward" "$local_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "Remote не является fast-forward продолжением локальной ветки"
    fail "Отказ: $REMOTE/$BRANCH нельзя применить fast-forward к локальному checkout"
    exit 1
  fi

  old_rev="$local_rev"
  log "Найден новый commit: ${old_rev:0:12} -> ${remote_rev:0:12}"

  # Не запускаем локальные post-merge hooks: deploy контролируется этим скриптом.
  if ! run_git -c core.hooksPath=/dev/null merge --ff-only "$remote_rev"; then
    write_state "merge-error" "$old_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "git merge --ff-only завершился ошибкой"
    fail "Не удалось выполнить fast-forward"
    exit 1
  fi

  candidate_rev="$(run_git rev-parse HEAD)"
  if [[ "$candidate_rev" != "$remote_rev" ]]; then
    write_state "revision-mismatch" "$candidate_rev" "$remote_rev" "$deployed_rev" "$blocked_rev" "HEAD после fast-forward не совпал с remote"
    fail "HEAD после обновления не совпадает с remote"
    exit 1
  fi
fi

log "Запускаю deploy ${candidate_rev:0:12}"
if DEPLOY_LOCK_PATH="$INNER_DEPLOY_LOCK_FILE" bash "$DEPLOY_SCRIPT" --yes; then
  blocked_rev=""
  blocked_at_epoch=""
  write_state "deployed" "$candidate_rev" "$remote_rev" "$candidate_rev" "" "Обновление и deploy завершены"
  log "Готово: ${candidate_rev:0:12}"
  exit 0
else
  deploy_code=$?
fi
log "Deploy ${candidate_rev:0:12} не прошёл; откатываю checkout на ${old_rev:0:12}"

# Checkout изначально был чистым, поэтому hard reset безопасен и восстанавливает
# ровно предыдущий уже работавший commit. Hooks отключены.
if ! run_git -c core.hooksPath=/dev/null reset --hard "$old_rev"; then
  write_state "rollback-git-failed" "$candidate_rev" "$remote_rev" "$deployed_rev" "$candidate_rev" "Не удалось вернуть checkout на предыдущий commit"
  fail "КРИТИЧНО: не удалось откатить git checkout"
  exit "$deploy_code"
fi

rollback_ok=0
if DEPLOY_LOCK_PATH="$INNER_DEPLOY_LOCK_FILE" bash "$DEPLOY_SCRIPT" --yes; then
  rollback_ok=1
  deployed_rev="$old_rev"
  log "Rollback deploy завершён: ${old_rev:0:12}"
else
  log "КРИТИЧНО: rollback deploy тоже завершился ошибкой; checkout возвращён на старый commit"
fi

blocked_rev="$candidate_rev"
blocked_at_epoch="$(date +%s)"
if [[ "$rollback_ok" -eq 1 ]]; then
  write_state "rolled-back" "$old_rev" "$remote_rev" "$old_rev" "$candidate_rev" "Новый commit провалил deploy; повтор после quarantine или при новом remote SHA"
else
  write_state "rollback-deploy-failed" "$old_rev" "$remote_rev" "$deployed_rev" "$candidate_rev" "Checkout откатан, но повторный deploy старой версии завершился ошибкой"
fi
exit "$deploy_code"
