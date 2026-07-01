#!/usr/bin/env bash
# Установка локальных git hooks, которые после git pull/merge/rebase обновляют /opt и перезапускают сервис.
# Hooks не хранятся в git, поэтому этот скрипт нужно выполнить один раз на рабочей машине.

set -Eeuo pipefail

DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_SERVICE_USER="gfsbot"

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
SERVICE_USER="${SERVICE_USER:-$DEFAULT_SERVICE_USER}"
ASSUME_YES=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
LOG_FILE="$REPO_ROOT/.git/gfs-profile-deploy.log"

if [[ -t 2 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_CYAN=""
fi

log() { printf '%s\n' "${C_BLUE}▶${C_RESET} $*" >&2; }
success() { printf '%s\n' "${C_GREEN}✓${C_RESET} $*" >&2; }
warn() { printf '%s\n' "${C_YELLOW}!${C_RESET} $*" >&2; }
fail() { printf '%s\n' "${C_RED}✗${C_RESET} $*" >&2; exit 1; }
section() { printf '\n%s\n' "${C_BOLD}${C_CYAN}== $* ==${C_RESET}" >&2; }

usage() {
  cat <<EOF
Установка git hooks для автообновления Telegram-бота

Использование:
  ./install_git_hooks.sh [опции]

Опции:
  --install-dir DIR       каталог установки, по умолчанию $DEFAULT_INSTALL_DIR
  --service-name NAME     имя systemd-сервиса, по умолчанию $DEFAULT_SERVICE_NAME
  --service-user USER     системный пользователь, по умолчанию $DEFAULT_SERVICE_USER
  --yes                   не задавать вопросы
  -h, --help              показать справку
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Неизвестная опция: $1" ;;
  esac
done

confirm() {
  local prompt="$1"
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    return 0
  fi
  local answer=""
  read -r -p "$prompt [Y/n]: " answer
  [[ -z "$answer" || "$answer" =~ ^[YyДд]$ ]]
}

require_git_repo() {
  [[ -d "$REPO_ROOT/.git" ]] || fail "$REPO_ROOT не является git checkout. Hooks можно ставить только в рабочем git-каталоге"
  [[ -f "$REPO_ROOT/deploy_telegram_bot.sh" ]] || fail "Не найден deploy_telegram_bot.sh"
}

write_hook() {
  local hook_name="$1"
  local hook_path="$HOOKS_DIR/$hook_name"

  cat > "$hook_path" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT='$REPO_ROOT'
export INSTALL_DIR='$INSTALL_DIR'
export SERVICE_NAME='$SERVICE_NAME'
export SERVICE_USER='$SERVICE_USER'
LOG_FILE='$LOG_FILE'
LOCK_FILE="/tmp/\${SERVICE_NAME}.git-hook.lock"

{
  echo
  echo "==== \$(date -u +%Y-%m-%dT%H:%M:%SZ) $hook_name: auto deploy start ===="
  echo "repo=\$REPO_ROOT"
  echo "rev=\$(git -C "\$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  exec 9>"\$LOCK_FILE"
  if ! flock -n 9; then
    echo "deploy skipped: lock is busy"
    exit 0
  fi
  bash "\$REPO_ROOT/deploy_telegram_bot.sh" --yes
  echo "==== \$(date -u +%Y-%m-%dT%H:%M:%SZ) $hook_name: auto deploy done ===="
} >> "\$LOG_FILE" 2>&1 || {
  echo "GFS Profile auto deploy failed. See \$LOG_FILE" >&2
  exit 1
}
EOF

  chmod +x "$hook_path"
  success "Установлен hook: $hook_path"
}

main() {
  section "Git auto-deploy hooks"
  require_git_repo
  echo "Источник:        $REPO_ROOT" >&2
  echo "Установка:       $INSTALL_DIR" >&2
  echo "Сервис:          ${SERVICE_NAME}.service" >&2
  echo "Пользователь:    $SERVICE_USER" >&2
  echo "Лог hooks:       $LOG_FILE" >&2
  confirm "Установить hooks post-merge, post-checkout, post-rewrite?" || fail "Отменено пользователем"

  mkdir -p "$HOOKS_DIR"
  write_hook post-merge
  write_hook post-rewrite
  write_hook post-checkout

  success "Готово. Теперь после git pull / merge / rebase / checkout будет выполняться deploy_telegram_bot.sh"
  warn "Если sudo требует пароль, hook может остановить git pull. Для полностью автоматического режима нужен passwordless sudo на systemctl/rsync/pip или запуск от root."
}

main "$@"
