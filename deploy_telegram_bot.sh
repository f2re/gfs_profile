#!/usr/bin/env bash
# Обновление установленного GFS Profile: Telegram + MAX + VK + web/API.

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
SKIP_TESTS=0
SKIP_COMMANDS=0
SKIP_WEBHOOKS=0
STATUS_ONLY=0
INSTALL_SYSTEM_PACKAGES=0
ADMIN_DB_BACKUP=""
ADMIN_DB_BACKUP_DIR=""
DEPLOY_LOCK=""
CURRENT_STAGE="инициализация"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
ENV_FILE="$INSTALL_DIR/.env"
VENV_DIR="$INSTALL_DIR/.venv"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
STATE_FILE="$INSTALL_DIR/.install-state"

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

on_error() {
  local code=$?
  printf '%s\n' "${C_RED}✗${C_RESET} Deploy остановлен на этапе «${CURRENT_STAGE}», строка ${BASH_LINENO[0]}, код ${code}" >&2
  exit "$code"
}
trap on_error ERR

usage() {
  cat <<EOF
Обновление GFS Profile Multi-Messenger Bot

Использование:
  sudo bash deploy_telegram_bot.sh [опции]

Опции:
  --install-dir DIR
  --service-name NAME
  --service-user USER
  --python PATH
  --yes
  --install-system-packages
  --skip-pip
  --skip-tests
  --skip-commands
  --skip-webhooks
  --no-restart
  --status
  -h, --help

Переменные:
  DEPLOY_LOCK_PATH      явный путь lock-файла
  DADATA_API_KEY        ключ DaData для неинтерактивной миграции
  MAX_BOT_TOKEN/...     можно задать/обновить при deploy
  VK_BOT_TOKEN/...      можно задать/обновить при deploy
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
    --skip-tests) SKIP_TESTS=1; shift ;;
    --skip-commands) SKIP_COMMANDS=1; shift ;;
    --skip-webhooks) SKIP_WEBHOOKS=1; shift ;;
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

if [[ "$(id -u)" -ne 0 ]]; then
  fail "Deploy изменяет /opt и systemd. Запустите: sudo bash deploy_telegram_bot.sh${ASSUME_YES:+ --yes}"
fi

run_root() { "$@"; }
run_user() { local user="$1"; shift; runuser -u "$user" -- "$@"; }
confirm() {
  if [[ "$ASSUME_YES" -eq 1 ]]; then return 0; fi
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
  if [[ -f "$ENV_FILE" ]]; then grep -Ev "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" >"$tmp" || true; fi
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  install -m 0640 -o root -g "$SERVICE_USER" "$tmp" "$ENV_FILE"
  rm -f "$tmp"
}
ensure_env_default() {
  local key="$1" value="$2"
  [[ -n "$(read_env_value "$key")" ]] || set_env_value "$key" "$value"
}
copy_env_if_set() {
  local key="$1" value="${!1:-}"
  [[ -z "$value" ]] || set_env_value "$key" "$value"
}
providers_require_dadata() { [[ ",$1," == *,dadata,* ]]; }
runtime_enabled() {
  case "$(read_env_value MESSENGER_RUNTIME_ENABLED | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|да) return 0 ;;
    *) return 1 ;;
  esac
}
run_with_env() {
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" bash -c '
    set -a
    source "$1"
    set +a
    cd "$2"
    shift 2
    exec "$@"
  ' _ "$ENV_FILE" "$INSTALL_DIR" "$@"
}
git_rev() { git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown; }
admin_db_path() {
  local raw
  raw="$(read_env_value TELEGRAM_ADMIN_DB)"; raw="${raw:-$DEFAULT_ADMIN_DB_RELATIVE}"
  if [[ "$raw" = /* ]]; then printf '%s' "$raw"; else printf '%s/%s' "$INSTALL_DIR" "$raw"; fi
}

resolve_deploy_lock() {
  local explicit git_dir
  explicit="${DEPLOY_LOCK_PATH:-}"
  if [[ -n "$explicit" ]]; then [[ "$explicit" = /* ]] || explicit="$REPO_ROOT/$explicit"; printf '%s' "$explicit"; return 0; fi
  if [[ -d /run/lock && -w /run/lock ]]; then printf '/run/lock/%s.deploy.lock' "$SERVICE_NAME"; return 0; fi
  git_dir="$(git -C "$REPO_ROOT" rev-parse --git-dir 2>/dev/null || true)"
  if [[ -n "$git_dir" ]]; then [[ "$git_dir" = /* ]] || git_dir="$REPO_ROOT/$git_dir"; printf '%s/%s.deploy.lock' "$git_dir" "$SERVICE_NAME"; return 0; fi
  fail "Не удалось выбрать каталог deploy lock"
}
acquire_deploy_lock() {
  local lock_dir legacy_lock
  command -v flock >/dev/null 2>&1 || fail "Не найден flock"
  DEPLOY_LOCK="$(resolve_deploy_lock)"; lock_dir="$(dirname "$DEPLOY_LOCK")"; mkdir -p "$lock_dir"
  [[ ! -L "$DEPLOY_LOCK" ]] || fail "Deploy lock не должен быть символической ссылкой: $DEPLOY_LOCK"
  [[ ! -e "$DEPLOY_LOCK" || -f "$DEPLOY_LOCK" ]] || fail "Путь deploy lock занят не файлом: $DEPLOY_LOCK"
  [[ -e "$DEPLOY_LOCK" ]] || install -m 0640 /dev/null "$DEPLOY_LOCK"
  exec 9<>"$DEPLOY_LOCK"; flock -n 9 || fail "Другой deploy уже выполняется: $DEPLOY_LOCK"
  legacy_lock="/tmp/${SERVICE_NAME}.deploy.lock"
  if [[ -e "$legacy_lock" ]]; then warn "Старый lock в /tmp больше не используется: $legacy_lock"; fi
  success "Deploy lock: $DEPLOY_LOCK"
}

cleanup() {
  if [[ -n "$ADMIN_DB_BACKUP_DIR" ]]; then rm -rf "$ADMIN_DB_BACKUP_DIR" 2>/dev/null || true; fi
  return 0
}
trap cleanup EXIT

print_status() {
  section "Состояние deploy"
  [[ -d "$REPO_ROOT/.git" ]] && success "Источник: $REPO_ROOT @ $(git_rev)" || warn "Источник не похож на git checkout"
  [[ -d "$INSTALL_DIR" ]] && success "Каталог: $INSTALL_DIR" || warn "Каталог отсутствует"
  if [[ -f "$ENV_FILE" ]]; then
    success ".env: $ENV_FILE"
    echo "  MESSENGER_RUNTIME_ENABLED=$(read_env_value MESSENGER_RUNTIME_ENABLED)" >&2
    echo "  MAX_BOT_TOKEN=$(mask_token "$(read_env_value MAX_BOT_TOKEN)")" >&2
    echo "  VK_BOT_TOKEN=$(mask_token "$(read_env_value VK_BOT_TOKEN)")" >&2
    echo "  GEOCODER_PROVIDERS=$(read_env_value GEOCODER_PROVIDERS)" >&2
    echo "  DADATA_API_KEY=$(mask_token "$(read_env_value DADATA_API_KEY)")" >&2
  else
    warn ".env отсутствует"
  fi
  [[ -x "$VENV_DIR/bin/python" ]] && success "venv: $VENV_DIR" || warn "venv отсутствует"
  local db; db="$(admin_db_path)"
  [[ -f "$db" ]] && success "admin DB: $db" || warn "admin DB пока не создана"
  if [[ -f "$UNIT_PATH" ]]; then
    grep -F "ExecStart=" "$UNIT_PATH" >&2 || true
  fi
  if command -v systemctl >/dev/null 2>&1; then systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null || true; fi
}

require_ready_install() {
  local file
  for file in messenger_launcher.py messenger_runtime.py messenger_config_check.py register_messenger_webhooks.py telegram_bot.py requirements.txt runtime_check.py geocoder_preflight.py prepare_basemap_cache.py register_telegram_commands.py; do
    [[ -f "$REPO_ROOT/$file" ]] || fail "В источнике нет $file"
  done
  [[ -d "$INSTALL_DIR" ]] || fail "Нет $INSTALL_DIR. Сначала выполните install_telegram_bot.sh"
  [[ -f "$ENV_FILE" ]] || fail "Нет $ENV_FILE"
  id "$SERVICE_USER" >/dev/null 2>&1 || fail "Нет пользователя $SERVICE_USER"
}
ensure_geocoder_config() {
  local providers key
  providers="${GEOCODER_PROVIDERS:-$(read_env_value GEOCODER_PROVIDERS)}"; providers="${providers:-$DEFAULT_GEOCODER_PROVIDERS}"
  set_env_value GEOCODER_PROVIDERS "$providers"
  if ! providers_require_dadata "$providers"; then return 0; fi
  key="${DADATA_API_KEY:-$(read_env_value DADATA_API_KEY)}"
  if [[ -z "$key" ]]; then
    [[ "$ASSUME_YES" -eq 0 ]] || fail "DADATA_API_KEY не задан. Передайте его в окружении или запустите deploy без --yes."
    read -r -s -p "Введите API-ключ DaData (Secret Key не нужен): " key; printf '\n' >&2
  fi
  [[ -n "$key" ]] || fail "DADATA_API_KEY пустой"
  set_env_value DADATA_API_KEY "$key"
  ensure_env_default DADATA_SUGGEST_URL "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
  ensure_env_default DADATA_TIMEOUT "12"
  ensure_env_default NOMINATIM_URL "https://nominatim.openstreetmap.org/search"
  success "DaData настроена: $(mask_token "$key")"
}
ensure_runtime_config() {
  local key
  for key in MAX_BOT_TOKEN MAX_WEBHOOK_URL MAX_WEBHOOK_SECRET VK_BOT_TOKEN VK_GROUP_ID VK_CALLBACK_URL VK_CALLBACK_SECRET VK_CONFIRMATION_CODE VK_API_VERSION MESSENGER_RUNTIME_ENABLED MESSENGER_RUNTIME_HOST MESSENGER_RUNTIME_PORT MESSENGER_RUNTIME_LOG_LEVEL MESSENGER_RUNTIME_ACCESS_LOG MESSENGER_PREFERENCES_DB TELEGRAM_PREFERENCES_DB MAX_CONCURRENT_GFS MAX_CONCURRENT_GEOCODE MAX_CONCURRENT_METEOGRAM; do
    copy_env_if_set "$key"
  done
  ensure_env_default MESSENGER_RUNTIME_ENABLED "1"
  ensure_env_default MESSENGER_RUNTIME_HOST "127.0.0.1"
  ensure_env_default MESSENGER_RUNTIME_PORT "8081"
  ensure_env_default MESSENGER_RUNTIME_LOG_LEVEL "info"
  ensure_env_default MESSENGER_RUNTIME_ACCESS_LOG "0"
  ensure_env_default MESSENGER_PREFERENCES_DB ".cache_gfs/messenger_preferences.sqlite3"
  ensure_env_default TELEGRAM_PREFERENCES_DB ".cache_gfs/telegram_preferences.sqlite3"
  ensure_env_default MAX_CONCURRENT_GFS "2"
  ensure_env_default MAX_CONCURRENT_GEOCODE "2"
  ensure_env_default MAX_CONCURRENT_METEOGRAM "2"
  ensure_env_default VK_API_VERSION "5.199"
}
install_system_packages() {
  if [[ "$INSTALL_SYSTEM_PACKAGES" -ne 1 ]]; then
    return 0
  fi
  apt-get update
  apt-get install -y python3 python3-venv python3-pip ca-certificates rsync fonts-dejavu-core fonts-dejavu-extra ffmpeg
  apt-get install -y python3-dev build-essential pkg-config libeccodes0 libeccodes-dev || warn "Дополнительные пакеты установлены не полностью"
  return 0
}
backup_admin_db() {
  local db; db="$(admin_db_path)"
  if [[ ! -f "$db" ]]; then return 0; fi
  ADMIN_DB_BACKUP_DIR="$(mktemp -d)"; ADMIN_DB_BACKUP="$ADMIN_DB_BACKUP_DIR/$(basename "$db")"; cp -a "$db" "$ADMIN_DB_BACKUP"
}
restore_admin_db() {
  local db dir; db="$(admin_db_path)"; dir="$(dirname "$db")"; mkdir -p "$dir"
  if [[ -n "$ADMIN_DB_BACKUP" && -f "$ADMIN_DB_BACKUP" && ! -f "$db" ]]; then cp -a "$ADMIN_DB_BACKUP" "$db"; fi
  if [[ -f "$db" ]]; then chown "$SERVICE_USER:$SERVICE_USER" "$db"; fi
  return 0
}
copy_project() {
  log "Синхронизирую $REPO_ROOT → $INSTALL_DIR"
  rsync -a --delete \
    --exclude '.git/' --exclude '.venv/' --exclude '.cache_gfs/' --exclude 'data/basemap/' \
    --exclude '.env' --exclude '.install-state' --exclude '__pycache__/' --exclude '*.pyc' \
    "$REPO_ROOT/" "$INSTALL_DIR/"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"; chown root:"$SERVICE_USER" "$ENV_FILE"; chmod 0640 "$ENV_FILE"
}
verify_sync() {
  local differences
  differences="$(rsync -rltD --checksum --dry-run --itemize-changes --delete \
    --exclude '.git/' --exclude '.venv/' --exclude '.cache_gfs/' --exclude 'data/basemap/' \
    --exclude '.env' --exclude '.install-state' --exclude '__pycache__/' --exclude '*.pyc' \
    "$REPO_ROOT/" "$INSTALL_DIR/")"
  if [[ -n "$differences" ]]; then printf '%s\n' "$differences" >&2; fail "Проверка синхронизации не пройдена"; fi
  success "Код в $INSTALL_DIR соответствует checkout $(git_rev)"
}
ensure_venv_and_deps() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then run_user "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"; fi
  if [[ "$SKIP_PIP" -eq 1 ]]; then
    warn "Обновление зависимостей пропущено"
    return 0
  fi
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --prefer-binary -r "$INSTALL_DIR/requirements.txt"
}
run_tests() {
  if [[ "$SKIP_TESTS" -eq 1 ]]; then
    warn "Unit tests пропущены"
    return 0
  fi
  run_with_env "$VENV_DIR/bin/python" -m unittest discover -s tests
}
validate_release() {
  log "Проверяю shell-синтаксис"
  bash -n "$INSTALL_DIR/install_telegram_bot.sh"
  bash -n "$INSTALL_DIR/deploy_telegram_bot.sh"
  bash -n "$INSTALL_DIR/install_messenger_runtime.sh"
  log "Проверяю Python runtime"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/runtime_check.py"
  log "Проверяю messenger env"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/messenger_config_check.py"
  log "Проверяю DaData до перезапуска"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/geocoder_preflight.py"
}
prepare_basemap_cache() {
  if run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/prepare_basemap_cache.py" --check >/dev/null 2>&1; then return 0; fi
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/prepare_basemap_cache.py" --resolution "${MAP_BASEMAP_RESOLUTION:-10m}" || warn "Не удалось подготовить basemap"
  return 0
}
write_service_unit() {
  cat <<EOF >"$UNIT_PATH"
[Unit]
Description=GFS Profile Multi-Messenger Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/messenger_launcher.py
Restart=always
RestartSec=5
User=$SERVICE_USER
Group=$SERVICE_USER
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$INSTALL_DIR

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$UNIT_PATH"
}
restart_service() {
  if [[ "$NO_RESTART" -eq 1 ]]; then warn "Перезапуск пропущен"; return 0; fi
  local before_pid after_pid attempt
  before_pid="$(systemctl show -p MainPID --value "${SERVICE_NAME}.service" 2>/dev/null || printf 0)"
  systemctl daemon-reload
  systemctl restart "${SERVICE_NAME}.service"
  for attempt in {1..10}; do
    if systemctl is-active --quiet "${SERVICE_NAME}.service"; then break; fi
    sleep 1
  done
  if ! systemctl is-active --quiet "${SERVICE_NAME}.service"; then journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true; fail "Сервис не стартовал"; fi
  after_pid="$(systemctl show -p MainPID --value "${SERVICE_NAME}.service" 2>/dev/null || printf 0)"
  if [[ "$before_pid" != "0" && "$after_pid" == "$before_pid" ]]; then fail "systemd сообщил active, но PID сервиса не изменился"; fi
  success "Сервис перезапущен: PID ${before_pid:-0} → ${after_pid:-0}"
}
wait_runtime_ready() {
  [[ "$NO_RESTART" -eq 0 ]] || return 0
  runtime_enabled || return 0
  local host port url attempt
  host="$(read_env_value MESSENGER_RUNTIME_HOST)"; host="${host:-127.0.0.1}"
  port="$(read_env_value MESSENGER_RUNTIME_PORT)"; port="${port:-8081}"
  [[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
  url="http://${host}:${port}/ready"
  for attempt in {1..20}; do
    if run_with_env "$VENV_DIR/bin/python" -c 'import requests,sys; r=requests.get(sys.argv[1], timeout=2); raise SystemExit(0 if r.status_code == 200 else 1)' "$url" >/dev/null 2>&1; then success "Runtime ready: $url"; return 0; fi
    sleep 1
  done
  journalctl -u "${SERVICE_NAME}.service" -n 100 --no-pager || true
  fail "Multi-messenger runtime не прошёл /ready: $url"
}
register_commands() {
  if [[ "$SKIP_COMMANDS" -eq 1 ]]; then
    warn "Регистрация Telegram-команд пропущена"
    return 0
  fi
  if run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/register_telegram_commands.py"; then success "Telegram-команды обновлены"; else warn "Не удалось зарегистрировать Telegram-команды"; fi
  return 0
}
register_webhooks() {
  if [[ "$SKIP_WEBHOOKS" -eq 1 ]]; then
    warn "Регистрация MAX/VK webhook пропущена"
    return 0
  fi
  runtime_enabled || return 0
  if run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/register_messenger_webhooks.py"; then success "MAX/VK webhook проверены и зарегистрированы"; return 0; fi
  fail "Runtime работает, но регистрация MAX/VK webhook не прошла; endpoint оставлен активным для безопасного повтора"
}
write_state() {
  local installed_at
  installed_at="$(grep '^installed_at=' "$STATE_FILE" 2>/dev/null | cut -d= -f2- || true)"
  cat <<EOF >"$STATE_FILE"
installed_at=$installed_at
last_deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source_repo=$REPO_ROOT
source_rev=$(git_rev)
install_dir=$INSTALL_DIR
service_name=$SERVICE_NAME
service_user=$SERVICE_USER
entrypoint=messenger_launcher.py
messenger_runtime=$(read_env_value MESSENGER_RUNTIME_ENABLED)
max_configured=$([[ -n "$(read_env_value MAX_BOT_TOKEN)" ]] && printf yes || printf no)
vk_configured=$([[ -n "$(read_env_value VK_BOT_TOKEN)" ]] && printf yes || printf no)
admin_db=$(admin_db_path)
geocoder_providers=$(read_env_value GEOCODER_PROVIDERS)
dadata=validated
deploy_lock=$DEPLOY_LOCK
runtime_check=ok
unit_tests=$([[ "$SKIP_TESTS" -eq 1 ]] && printf skipped || printf ok)
EOF
  chown "$SERVICE_USER:$SERVICE_USER" "$STATE_FILE"
}

main() {
  section "Deploy GFS Profile Multi-Messenger Bot"
  CURRENT_STAGE="чтение состояния"; print_status
  [[ "$STATUS_ONLY" -eq 0 ]] || exit 0
  CURRENT_STAGE="проверка установленного приложения"; require_ready_install
  confirm "Обновить $INSTALL_DIR и перезапустить ${SERVICE_NAME}.service?" || fail "Отменено"
  CURRENT_STAGE="получение блокировки"; acquire_deploy_lock
  CURRENT_STAGE="опциональные системные пакеты"; log "Этап: системные пакеты"; install_system_packages
  CURRENT_STAGE="конфигурация DaData"; log "Этап: конфигурация DaData"; ensure_geocoder_config
  CURRENT_STAGE="конфигурация messenger runtime"; log "Этап: Telegram/MAX/VK runtime"; ensure_runtime_config
  CURRENT_STAGE="резервная копия admin DB"; log "Этап: резервная копия admin DB"; backup_admin_db
  CURRENT_STAGE="синхронизация кода"; log "Этап: копирование кода в $INSTALL_DIR"; copy_project; restore_admin_db; verify_sync
  CURRENT_STAGE="обновление виртуального окружения"; log "Этап: зависимости Python"; ensure_venv_and_deps
  CURRENT_STAGE="unit tests"; log "Этап: unit tests"; run_tests
  CURRENT_STAGE="runtime preflight"; log "Этап: runtime и DaData preflight"; validate_release
  CURRENT_STAGE="подготовка картографической подложки"; log "Этап: basemap"; prepare_basemap_cache
  CURRENT_STAGE="systemd unit"; log "Этап: multi-messenger systemd unit"; write_service_unit
  CURRENT_STAGE="перезапуск systemd"; log "Этап: перезапуск ${SERVICE_NAME}.service"; restart_service
  CURRENT_STAGE="readiness"; log "Этап: /ready"; wait_runtime_ready
  CURRENT_STAGE="регистрация Telegram-команд"; log "Этап: регистрация Telegram-команд"; register_commands
  CURRENT_STAGE="регистрация MAX/VK webhook"; log "Этап: регистрация MAX/VK webhook"; register_webhooks
  CURRENT_STAGE="запись состояния"; write_state
  CURRENT_STAGE="завершено"; success "Deploy завершён: $(git_rev)"
  systemctl status "${SERVICE_NAME}.service" --no-pager --lines=5 || true
}

main "$@"
