#!/usr/bin/env bash
# Обновление установленного Telegram-бота из текущего git checkout в /opt.
# Используется вручную и из git hooks после git pull / merge / rebase.

set -Eeuo pipefail

DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_SERVICE_USER="gfsbot"

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
SERVICE_USER="${SERVICE_USER:-$DEFAULT_SERVICE_USER}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ASSUME_YES=0
NO_RESTART=0
SKIP_PIP=0
STATUS_ONLY=0
SKIP_COMMANDS=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
ENV_FILE="$INSTALL_DIR/.env"
VENV_DIR="$INSTALL_DIR/.venv"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
STATE_FILE="$INSTALL_DIR/.install-state"
DEPLOY_LOCK="/tmp/${SERVICE_NAME}.deploy.lock"

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
Обновление установленного Telegram-бота из git checkout

Использование:
  ./deploy_telegram_bot.sh [опции]

Опции:
  --install-dir DIR       каталог установки, по умолчанию $DEFAULT_INSTALL_DIR
  --service-name NAME     имя systemd-сервиса, по умолчанию $DEFAULT_SERVICE_NAME
  --service-user USER     системный пользователь, по умолчанию $DEFAULT_SERVICE_USER
  --python PATH           Python-интерпретатор для создания venv, если venv отсутствует
  --yes                   не задавать подтверждающие вопросы
  --skip-pip              не обновлять pip-зависимости
  --skip-commands         не регистрировать Telegram slash-команды
  --no-restart            не перезапускать systemd-сервис
  --status                только показать состояние
  -h, --help              показать справку
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    --skip-pip) SKIP_PIP=1; shift ;;
    --skip-commands) SKIP_COMMANDS=1; shift ;;
    --no-restart) NO_RESTART=1; shift ;;
    --status) STATUS_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Неизвестная опция: $1" ;;
  esac
done

ENV_FILE="$INSTALL_DIR/.env"
VENV_DIR="$INSTALL_DIR/.venv"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
STATE_FILE="$INSTALL_DIR/.install-state"
DEPLOY_LOCK="/tmp/${SERVICE_NAME}.deploy.lock"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

run_root() {
  if [[ -n "$SUDO" ]]; then
    sudo "$@"
  else
    "$@"
  fi
}

run_user() {
  local user="$1"
  shift
  if [[ -n "$SUDO" ]]; then
    sudo -u "$user" "$@"
  else
    runuser -u "$user" -- "$@"
  fi
}

confirm() {
  local prompt="$1"
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    return 0
  fi
  local answer=""
  read -r -p "$prompt [Y/n]: " answer
  [[ -z "$answer" || "$answer" =~ ^[YyДд]$ ]]
}

git_rev() {
  if git -C "$REPO_ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
    git -C "$REPO_ROOT" rev-parse --short HEAD
  else
    printf 'unknown'
  fi
}

print_status() {
  section "Состояние deploy"
  [[ -d "$REPO_ROOT/.git" ]] && success "Источник git: $REPO_ROOT @ $(git_rev)" || warn "Источник не похож на git checkout: $REPO_ROOT"
  [[ -d "$INSTALL_DIR" ]] && success "Каталог установки: $INSTALL_DIR" || warn "Каталог установки отсутствует: $INSTALL_DIR"
  [[ -f "$ENV_FILE" ]] && success ".env найден: $ENV_FILE" || warn ".env не найден: $ENV_FILE"
  [[ -x "$VENV_DIR/bin/python" ]] && success "venv найден: $VENV_DIR" || warn "venv не найден: $VENV_DIR"
  [[ -f "$UNIT_PATH" ]] && success "systemd unit найден: $UNIT_PATH" || warn "systemd unit не найден: $UNIT_PATH"
  [[ -f "$INSTALL_DIR/register_telegram_commands.py" ]] && success "registrar команд найден" || warn "registrar команд не найден в $INSTALL_DIR"
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
    printf 'Активность сервиса: ' >&2
    systemctl is-active "${SERVICE_NAME}.service" >&2 || true
  fi
  [[ -f "$STATE_FILE" ]] && { echo "Состояние установки:" >&2; sed 's/^/  /' "$STATE_FILE" >&2 || true; }
}

require_ready_install() {
  [[ -f "$REPO_ROOT/telegram_bot.py" ]] || fail "В источнике нет telegram_bot.py: $REPO_ROOT"
  [[ -f "$REPO_ROOT/requirements.txt" ]] || fail "В источнике нет requirements.txt: $REPO_ROOT"
  [[ -f "$REPO_ROOT/gfs_core.py" ]] || fail "В источнике нет gfs_core.py: $REPO_ROOT"
  [[ -f "$REPO_ROOT/runtime_check.py" ]] || fail "В источнике нет runtime_check.py: $REPO_ROOT"
  [[ -f "$REPO_ROOT/register_telegram_commands.py" ]] || fail "В источнике нет register_telegram_commands.py: $REPO_ROOT"
  [[ -d "$INSTALL_DIR" ]] || fail "Каталог $INSTALL_DIR не существует. Сначала выполните bash install_telegram_bot.sh"
  [[ -f "$ENV_FILE" ]] || fail "Нет $ENV_FILE. Сначала выполните первичную установку, чтобы задать TELEGRAM_BOT_TOKEN"
  id "$SERVICE_USER" >/dev/null 2>&1 || fail "Пользователь $SERVICE_USER не найден. Сначала выполните первичную установку"
}

copy_project() {
  log "Синхронизирую код $REPO_ROOT → $INSTALL_DIR"
  if command -v rsync >/dev/null 2>&1; then
    run_root rsync -a --delete \
      --exclude '.git/' \
      --exclude '.venv/' \
      --exclude '.cache_gfs/' \
      --exclude '.env' \
      --exclude '.install-state' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      "$REPO_ROOT/" "$INSTALL_DIR/"
  else
    warn "rsync не найден. Использую tar-copy без удаления старых лишних файлов."
    (cd "$REPO_ROOT" && tar \
      --exclude='./.git' \
      --exclude='./.venv' \
      --exclude='./.cache_gfs' \
      --exclude='./.env' \
      --exclude='./.install-state' \
      --exclude='*/__pycache__' \
      --exclude='*.pyc' \
      -cf - .) | run_root tar -xf - -C "$INSTALL_DIR"
  fi
  run_root chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}

ensure_venv_and_deps() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "venv отсутствует, создаю $VENV_DIR"
    run_user "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  if [[ "$SKIP_PIP" -eq 1 ]]; then
    warn "Обновление Python-зависимостей пропущено (--skip-pip)"
    return 0
  fi

  log "Обновляю Python-зависимости"
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --prefer-binary -r "$INSTALL_DIR/requirements.txt"
}

runtime_check() {
  log "Проверяю runtime-зависимости до перезапуска сервиса"
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" MPLBACKEND=Agg "$VENV_DIR/bin/python" "$INSTALL_DIR/runtime_check.py"
}

restart_service() {
  [[ "$NO_RESTART" -eq 1 ]] && { warn "Перезапуск сервиса пропущен (--no-restart)"; return 0; }
  [[ -f "$UNIT_PATH" ]] || fail "systemd unit отсутствует: $UNIT_PATH"
  log "Перезапускаю ${SERVICE_NAME}.service"
  run_root systemctl daemon-reload
  run_root systemctl restart "${SERVICE_NAME}.service"
  sleep 2
  if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    success "Сервис активен: ${SERVICE_NAME}.service"
  else
    warn "Сервис не активен. Последние строки журнала:"
    run_root journalctl -u "${SERVICE_NAME}.service" -n 60 --no-pager || true
    fail "Deploy выполнен, но сервис не стартовал"
  fi
}

register_telegram_commands() {
  if [[ "$SKIP_COMMANDS" -eq 1 ]]; then
    warn "Регистрация Telegram-команд пропущена (--skip-commands)"
    return 0
  fi
  if [[ ! -f "$INSTALL_DIR/register_telegram_commands.py" ]]; then
    warn "register_telegram_commands.py не найден; меню Telegram не обновлено"
    return 0
  fi
  log "Регистрирую Telegram slash-команды"
  if run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" MPLBACKEND=Agg "$VENV_DIR/bin/python" "$INSTALL_DIR/register_telegram_commands.py"; then
    success "Telegram-команды зарегистрированы"
  else
    warn "Не удалось зарегистрировать Telegram-команды. Сервис уже обновлён; повторите вручную: cd $INSTALL_DIR && $VENV_DIR/bin/python register_telegram_commands.py"
  fi
}

write_state() {
  local rev installed_at
  rev="$(git_rev)"
  installed_at="$(grep '^installed_at=' "$STATE_FILE" 2>/dev/null | cut -d= -f2- || true)"
  cat <<EOF | run_root tee "$STATE_FILE" >/dev/null
installed_at=$installed_at
last_deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source_repo=$REPO_ROOT
source_rev=$rev
install_dir=$INSTALL_DIR
service_name=$SERVICE_NAME
service_user=$SERVICE_USER
venv=$VENV_DIR
unit=$UNIT_PATH
runtime_check=ok
telegram_commands=attempted
EOF
  run_root chown "$SERVICE_USER:$SERVICE_USER" "$STATE_FILE"
}

main() {
  section "Deploy GFS Profile Bot"
  print_status
  [[ "$STATUS_ONLY" -eq 1 ]] && exit 0
  require_ready_install
  confirm "Обновить $INSTALL_DIR из $REPO_ROOT и перезапустить ${SERVICE_NAME}.service?" || fail "Отменено пользователем"

  exec 9>"$DEPLOY_LOCK"
  if ! flock -n 9; then
    fail "Другой deploy уже выполняется: $DEPLOY_LOCK"
  fi

  copy_project
  ensure_venv_and_deps
  runtime_check
  restart_service
  register_telegram_commands
  write_state
  success "Deploy завершён: $(git_rev)"
}

main "$@"
