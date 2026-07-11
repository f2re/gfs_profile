#!/usr/bin/env bash
# Обновление установленного Telegram-бота из текущего checkout.

set -Eeuo pipefail

DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_SERVICE_USER="gfsbot"
DEFAULT_ADMIN_DB_RELATIVE=".cache_gfs/admin_stats.sqlite3"
DEFAULT_GEOCODER_PROVIDERS="dadata,local,nominatim"

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

usage() {
  cat <<EOF
Обновление установленного Telegram-бота

Использование:
  ./deploy_telegram_bot.sh [опции]

Опции:
  --install-dir DIR
  --service-name NAME
  --service-user USER
  --python PATH
  --yes                         без вопросов; отсутствующий DADATA_API_KEY = ошибка
  --install-system-packages
  --skip-pip
  --skip-commands
  --no-restart
  --status
  -h, --help

При миграции старой установки deploy запросит DADATA_API_KEY и сохранит его в .env.
Для неинтерактивного deploy передайте DADATA_API_KEY через окружение.
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

if [[ "$(id -u)" -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi
run_root() { if [[ -n "$SUDO" ]]; then sudo "$@"; else "$@"; fi; }
run_user() {
  local user="$1"; shift
  if [[ -n "$SUDO" ]]; then sudo -u "$user" "$@"; else runuser -u "$user" -- "$@"; fi
}
confirm() {
  [[ "$ASSUME_YES" -eq 1 ]] && return 0
  local answer=""; read -r -p "$1 [Y/n]: " answer
  [[ -z "$answer" || "$answer" =~ ^[YyДд]$ ]]
}
mask_token() {
  local value="${1:-}"
  if [[ -z "$value" ]]; then printf 'не задан'; elif [[ ${#value} -le 10 ]]; then printf '***'; else printf '%s…%s' "${value:0:5}" "${value: -4}"; fi
}
read_env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true
}
set_env_value() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -Ev "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  run_root install -m 0640 -o root -g "$SERVICE_USER" "$tmp" "$ENV_FILE"
  rm -f "$tmp"
}
providers_require_dadata() { [[ ",$1," == *,dadata,* ]]; }
run_with_env() {
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" bash -c 'set -a; source "$1"; set +a; shift; exec "$@"' _ "$ENV_FILE" "$@"
}
git_rev() { git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown; }
admin_db_path() {
  local raw; raw="$(read_env_value TELEGRAM_ADMIN_DB)"; raw="${raw:-$DEFAULT_ADMIN_DB_RELATIVE}"
  [[ "$raw" = /* ]] && printf '%s' "$raw" || printf '%s/%s' "$INSTALL_DIR" "$raw"
}
cleanup() { [[ -z "$ADMIN_DB_BACKUP_DIR" ]] || run_root rm -rf "$ADMIN_DB_BACKUP_DIR" 2>/dev/null || true; }
trap cleanup EXIT

print_status() {
  section "Состояние deploy"
  [[ -d "$REPO_ROOT/.git" ]] && success "Источник: $REPO_ROOT @ $(git_rev)" || warn "Источник не похож на git checkout"
  [[ -d "$INSTALL_DIR" ]] && success "Каталог: $INSTALL_DIR" || warn "Каталог отсутствует"
  if [[ -f "$ENV_FILE" ]]; then
    success ".env: $ENV_FILE"
    echo "  GEOCODER_PROVIDERS=$(read_env_value GEOCODER_PROVIDERS)" >&2
    echo "  DADATA_API_KEY=$(mask_token "$(read_env_value DADATA_API_KEY)")" >&2
  else
    warn ".env отсутствует"
  fi
  [[ -x "$VENV_DIR/bin/python" ]] && success "venv: $VENV_DIR" || warn "venv отсутствует"
  [[ -f "$(admin_db_path)" ]] && success "admin DB: $(admin_db_path)" || warn "admin DB пока не создана"
  if command -v systemctl >/dev/null 2>&1; then systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null || true; fi
}

require_ready_install() {
  for file in telegram_bot.py requirements.txt runtime_check.py geocoder_preflight.py prepare_basemap_cache.py register_telegram_commands.py; do
    [[ -f "$REPO_ROOT/$file" ]] || fail "В источнике нет $file"
  done
  [[ -d "$INSTALL_DIR" ]] || fail "Нет $INSTALL_DIR. Сначала выполните install_telegram_bot.sh"
  [[ -f "$ENV_FILE" ]] || fail "Нет $ENV_FILE"
  id "$SERVICE_USER" >/dev/null 2>&1 || fail "Нет пользователя $SERVICE_USER"
}
ensure_geocoder_config() {
  local providers key
  providers="${GEOCODER_PROVIDERS:-$(read_env_value GEOCODER_PROVIDERS)}"
  providers="${providers:-$DEFAULT_GEOCODER_PROVIDERS}"
  set_env_value GEOCODER_PROVIDERS "$providers"
  providers_require_dadata "$providers" || return

  key="${DADATA_API_KEY:-$(read_env_value DADATA_API_KEY)}"
  if [[ -z "$key" ]]; then
    [[ "$ASSUME_YES" -eq 0 ]] || fail "DADATA_API_KEY не задан. Передайте его в окружении или запустите deploy без --yes."
    read -r -s -p "Введите API-ключ DaData (Secret Key не нужен): " key
    printf '\n' >&2
  fi
  [[ -n "$key" ]] || fail "DADATA_API_KEY пустой"
  set_env_value DADATA_API_KEY "$key"
  [[ -n "$(read_env_value DADATA_SUGGEST_URL)" ]] || set_env_value DADATA_SUGGEST_URL "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
  [[ -n "$(read_env_value DADATA_TIMEOUT)" ]] || set_env_value DADATA_TIMEOUT "12"
  [[ -n "$(read_env_value NOMINATIM_URL)" ]] || set_env_value NOMINATIM_URL "https://nominatim.openstreetmap.org/search"
  success "DaData настроена: $(mask_token "$key")"
}
install_system_packages() {
  [[ "$INSTALL_SYSTEM_PACKAGES" -eq 1 ]] || return
  run_root apt-get update
  run_root apt-get install -y python3 python3-venv python3-pip ca-certificates rsync fonts-dejavu-core fonts-dejavu-extra ffmpeg
  run_root apt-get install -y python3-dev build-essential pkg-config libeccodes0 libeccodes-dev || warn "Дополнительные пакеты установлены не полностью"
}
backup_admin_db() {
  local db; db="$(admin_db_path)"
  [[ -f "$db" ]] || return
  ADMIN_DB_BACKUP_DIR="$(mktemp -d)"
  ADMIN_DB_BACKUP="$ADMIN_DB_BACKUP_DIR/$(basename "$db")"
  run_root cp -a "$db" "$ADMIN_DB_BACKUP"
}
restore_admin_db() {
  local db dir; db="$(admin_db_path)"; dir="$(dirname "$db")"
  run_root mkdir -p "$dir"
  if [[ -n "$ADMIN_DB_BACKUP" && -f "$ADMIN_DB_BACKUP" && ! -f "$db" ]]; then run_root cp -a "$ADMIN_DB_BACKUP" "$db"; fi
  [[ ! -f "$db" ]] || run_root chown "$SERVICE_USER:$SERVICE_USER" "$db"
}
copy_project() {
  log "Синхронизирую $REPO_ROOT → $INSTALL_DIR"
  run_root rsync -a --delete \
    --exclude '.git/' --exclude '.venv/' --exclude '.cache_gfs/' --exclude 'data/basemap/' \
    --exclude '.env' --exclude '.install-state' --exclude '__pycache__/' --exclude '*.pyc' \
    "$REPO_ROOT/" "$INSTALL_DIR/"
  run_root chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
  run_root chown root:"$SERVICE_USER" "$ENV_FILE"
  run_root chmod 0640 "$ENV_FILE"
}
ensure_venv_and_deps() {
  [[ -x "$VENV_DIR/bin/python" ]] || run_user "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
  [[ "$SKIP_PIP" -eq 0 ]] || return
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --prefer-binary -r "$INSTALL_DIR/requirements.txt"
}
validate_release() {
  log "Проверяю shell-синтаксис"
  bash -n "$INSTALL_DIR/install_telegram_bot.sh"
  bash -n "$INSTALL_DIR/deploy_telegram_bot.sh"
  log "Проверяю Python runtime"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/runtime_check.py"
  log "Проверяю DaData до перезапуска"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/geocoder_preflight.py"
}
prepare_basemap_cache() {
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/prepare_basemap_cache.py" --check >/dev/null 2>&1 && return
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/prepare_basemap_cache.py" --resolution "${MAP_BASEMAP_RESOLUTION:-10m}" || warn "Не удалось подготовить basemap"
}
restart_service() {
  [[ "$NO_RESTART" -eq 0 ]] || { warn "Перезапуск пропущен"; return; }
  run_root systemctl daemon-reload
  run_root systemctl restart "${SERVICE_NAME}.service"
  sleep 2
  systemctl is-active --quiet "${SERVICE_NAME}.service" || { run_root journalctl -u "${SERVICE_NAME}.service" -n 60 --no-pager || true; fail "Сервис не стартовал"; }
}
register_commands() {
  [[ "$SKIP_COMMANDS" -eq 0 ]] || return
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/register_telegram_commands.py" || warn "Не удалось зарегистрировать команды"
}
write_state() {
  local installed_at; installed_at="$(grep '^installed_at=' "$STATE_FILE" 2>/dev/null | cut -d= -f2- || true)"
  cat <<EOF | run_root tee "$STATE_FILE" >/dev/null
installed_at=$installed_at
last_deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source_repo=$REPO_ROOT
source_rev=$(git_rev)
install_dir=$INSTALL_DIR
service_name=$SERVICE_NAME
service_user=$SERVICE_USER
admin_db=$(admin_db_path)
geocoder_providers=$(read_env_value GEOCODER_PROVIDERS)
dadata=validated
runtime_check=ok
EOF
  run_root chown "$SERVICE_USER:$SERVICE_USER" "$STATE_FILE"
}

main() {
  section "Deploy GFS Profile Bot"
  print_status
  [[ "$STATUS_ONLY" -eq 0 ]] || exit 0
  require_ready_install
  confirm "Обновить $INSTALL_DIR и перезапустить ${SERVICE_NAME}.service?" || fail "Отменено"
  exec 9>"$DEPLOY_LOCK"; flock -n 9 || fail "Другой deploy уже выполняется"
  install_system_packages
  ensure_geocoder_config
  backup_admin_db
  copy_project
  restore_admin_db
  ensure_venv_and_deps
  validate_release
  prepare_basemap_cache
  restart_service
  register_commands
  write_state
  success "Deploy завершён: $(git_rev)"
}

main "$@"
