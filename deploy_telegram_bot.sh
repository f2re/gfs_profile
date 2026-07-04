#!/usr/bin/env bash
# Обновление установленного Telegram-бота из текущего git checkout в /opt.
# Используется вручную и из git hooks после git pull / merge / rebase.

set -Eeuo pipefail

DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_SERVICE_USER="gfsbot"
DEFAULT_ADMIN_DB_RELATIVE=".cache_gfs/admin_stats.sqlite3"

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
SERVICE_USER="${SERVICE_USER:-$DEFAULT_SERVICE_USER}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ASSUME_YES=0
NO_RESTART=0
SKIP_PIP=0
STATUS_ONLY=0
SKIP_COMMANDS=0
INSTALL_SYSTEM_PACKAGES=0
ADMIN_DB_BACKUP=""
ADMIN_DB_BACKUP_DIR=""

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

cleanup() {
  if [[ -n "$ADMIN_DB_BACKUP_DIR" && -d "$ADMIN_DB_BACKUP_DIR" ]]; then
    run_root rm -rf "$ADMIN_DB_BACKUP_DIR" 2>/dev/null || rm -rf "$ADMIN_DB_BACKUP_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT

usage() {
  cat <<EOF
Обновление установленного Telegram-бота из git checkout

Использование:
  ./deploy_telegram_bot.sh [опции]

Опции:
  --install-dir DIR             каталог установки, по умолчанию $DEFAULT_INSTALL_DIR
  --service-name NAME           имя systemd-сервиса, по умолчанию $DEFAULT_SERVICE_NAME
  --service-user USER           системный пользователь, по умолчанию $DEFAULT_SERVICE_USER
  --python PATH                 Python-интерпретатор для создания venv, если venv отсутствует
  --yes                         не задавать подтверждающие вопросы
  --install-system-packages     поставить/обновить apt-пакеты: Python, rsync, шрифты, ffmpeg, eccodes
  --skip-pip                    не обновлять pip-зависимости
  --skip-commands               не регистрировать Telegram slash-команды
  --no-restart                  не перезапускать systemd-сервис
  --status                      только показать состояние
  -h, --help                    показать справку
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    --install-system-packages) INSTALL_SYSTEM_PACKAGES=1; shift ;;
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

read_env_value() {
  local key="$1"
  local line value
  [[ -f "$ENV_FILE" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  value="$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  printf '%s' "$value"
}

admin_db_path() {
  local raw
  raw="$(read_env_value TELEGRAM_ADMIN_DB || true)"
  [[ -n "$raw" ]] || raw="$DEFAULT_ADMIN_DB_RELATIVE"
  if [[ "$raw" = /* ]]; then
    printf '%s' "$raw"
  else
    printf '%s/%s' "$INSTALL_DIR" "$raw"
  fi
}

ffmpeg_status() {
  if command -v ffmpeg >/dev/null 2>&1; then
    ffmpeg -version 2>/dev/null | head -n 1 || true
  else
    return 1
  fi
}

print_status() {
  local admin_db ffmpeg_line
  admin_db="$(admin_db_path)"
  section "Состояние deploy"
  [[ -d "$REPO_ROOT/.git" ]] && success "Источник git: $REPO_ROOT @ $(git_rev)" || warn "Источник не похож на git checkout: $REPO_ROOT"
  [[ -d "$INSTALL_DIR" ]] && success "Каталог установки: $INSTALL_DIR" || warn "Каталог установки отсутствует: $INSTALL_DIR"
  [[ -f "$ENV_FILE" ]] && success ".env найден: $ENV_FILE" || warn ".env не найден: $ENV_FILE"
  [[ -x "$VENV_DIR/bin/python" ]] && success "venv найден: $VENV_DIR" || warn "venv не найден: $VENV_DIR"
  [[ -f "$UNIT_PATH" ]] && success "systemd unit найден: $UNIT_PATH" || warn "systemd unit не найден: $UNIT_PATH"
  [[ -f "$INSTALL_DIR/register_telegram_commands.py" ]] && success "registrar команд найден" || warn "registrar команд не найден в $INSTALL_DIR"
  [[ -f "$admin_db" ]] && success "admin DB найден: $admin_db" || warn "admin DB пока не создан: $admin_db"
  if ffmpeg_line="$(ffmpeg_status)"; then
    success "ffmpeg: $ffmpeg_line"
  else
    warn "ffmpeg не найден: /map mode=gif будет использовать GIF fallback вместо MP4"
  fi
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
  [[ -f "$REPO_ROOT/prepare_basemap_cache.py" ]] || fail "В источнике нет prepare_basemap_cache.py: $REPO_ROOT"
  [[ -f "$REPO_ROOT/register_telegram_commands.py" ]] || fail "В источнике нет register_telegram_commands.py: $REPO_ROOT"
  [[ -d "$INSTALL_DIR" ]] || fail "Каталог $INSTALL_DIR не существует. Сначала выполните bash install_telegram_bot.sh"
  [[ -f "$ENV_FILE" ]] || fail "Нет $ENV_FILE. Сначала выполните первичную установку, чтобы задать TELEGRAM_BOT_TOKEN"
  id "$SERVICE_USER" >/dev/null 2>&1 || fail "Пользователь $SERVICE_USER не найден. Сначала выполните первичную установку"
}

install_system_packages() {
  [[ "$INSTALL_SYSTEM_PACKAGES" -eq 1 ]] || return 0
  command -v apt-get >/dev/null 2>&1 || { warn "apt-get не найден. Системные пакеты не обновлены."; return 0; }
  log "Обновляю apt и ставлю системные пакеты runtime"
  run_root apt-get update
  local base_packages=(python3 python3-venv python3-pip ca-certificates rsync fonts-dejavu-core fonts-dejavu-extra ffmpeg)
  run_root apt-get install -y "${base_packages[@]}"
  local optional_packages=(python3-dev build-essential pkg-config libeccodes0 libeccodes-dev)
  if ! run_root apt-get install -y "${optional_packages[@]}"; then
    warn "Часть дополнительных пакетов не установлена. Если cfgrib/eccodes или pip wheel не соберётся, установите вручную: ${optional_packages[*]}"
  fi
}

backup_admin_db() {
  local admin_db backup
  admin_db="$(admin_db_path)"
  if [[ ! -f "$admin_db" ]]; then
    warn "admin DB пока не создана: $admin_db"
    return 0
  fi
  ADMIN_DB_BACKUP_DIR="$(mktemp -d)"
  backup="$ADMIN_DB_BACKUP_DIR/$(basename "$admin_db")"
  run_root cp -a "$admin_db" "$backup"
  ADMIN_DB_BACKUP="$backup"
  success "admin DB сохранена перед rsync: $admin_db"
}

restore_admin_db() {
  local admin_db admin_dir
  admin_db="$(admin_db_path)"
  admin_dir="$(dirname "$admin_db")"
  run_root mkdir -p "$admin_dir"
  if [[ -n "$ADMIN_DB_BACKUP" && -f "$ADMIN_DB_BACKUP" && ! -f "$admin_db" ]]; then
    run_root cp -a "$ADMIN_DB_BACKUP" "$admin_db"
    success "admin DB восстановлена после rsync: $admin_db"
  fi
  if [[ -f "$admin_db" ]]; then
    run_root chown "$SERVICE_USER:$SERVICE_USER" "$admin_db"
    success "admin DB сохранена: $admin_db"
  else
    run_root chown "$SERVICE_USER:$SERVICE_USER" "$admin_dir" 2>/dev/null || true
    warn "admin DB будет создана ботом при первом учёте: $admin_db"
  fi
}

copy_project() {
  log "Синхронизирую код $REPO_ROOT → $INSTALL_DIR"
  if command -v rsync >/dev/null 2>&1; then
    run_root rsync -a --delete \
      --exclude '.git/' \
      --exclude '.venv/' \
      --exclude '.cache_gfs/' \
      --exclude 'data/basemap/' \
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
      --exclude='./data/basemap' \
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
  if ! ffmpeg_status >/dev/null 2>&1; then
    warn "ffmpeg не найден. Бот работает, но качественная MP4-анимация /map недоступна. Выполните: bash deploy_telegram_bot.sh --install-system-packages"
  fi
}

prepare_basemap_cache() {
  log "Проверяю офлайн-подложку Natural Earth"
  if run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" MPLBACKEND=Agg "$VENV_DIR/bin/python" "$INSTALL_DIR/prepare_basemap_cache.py" --check; then
    success "Офлайн-подложка карт уже готова"
    return 0
  fi
  if run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" MPLBACKEND=Agg "$VENV_DIR/bin/python" "$INSTALL_DIR/prepare_basemap_cache.py" --resolution "${MAP_BASEMAP_RESOLUTION:-10m}"; then
    success "Офлайн-подложка карт подготовлена"
  else
    warn "Не удалось подготовить basemap cache. Deploy продолжится; карта использует fallback при отсутствии кэша."
  fi
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
  local rev installed_at admin_db ffmpeg_line
  rev="$(git_rev)"
  admin_db="$(admin_db_path)"
  ffmpeg_line="$(ffmpeg_status || true)"
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
admin_db=$admin_db
runtime_check=ok
ffmpeg=${ffmpeg_line:-missing}
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

  install_system_packages
  backup_admin_db
  copy_project
  restore_admin_db
  ensure_venv_and_deps
  runtime_check
  prepare_basemap_cache
  restart_service
  register_telegram_commands
  write_state
  success "Deploy завершён: $(git_rev)"
}

main "$@"
