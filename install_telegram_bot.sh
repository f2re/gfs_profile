#!/usr/bin/env bash
# Полная установка GFS Profile: Telegram + MAX + VK + web/API.

set -Eeuo pipefail

APP_NAME="GFS Profile Multi-Messenger Bot"
DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_SERVICE_USER="gfsbot"
DEFAULT_GEOCODER_PROVIDERS="dadata,local,nominatim"

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
SERVICE_USER="${SERVICE_USER:-$DEFAULT_SERVICE_USER}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ASSUME_YES=0
SKIP_APT=0
SKIP_COMMANDS=0
SKIP_WEBHOOKS=0
NO_START=0
STATUS_ONLY=0
EXTRA_READ_WRITE_PATHS=""

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

usage() {
  cat <<EOF
$APP_NAME — установка

Использование:
  ./install_telegram_bot.sh [опции]

Опции:
  --install-dir DIR       каталог установки, по умолчанию $DEFAULT_INSTALL_DIR
  --service-name NAME     имя systemd-сервиса
  --service-user USER     системный пользователь
  --python PATH           Python-интерпретатор
  --yes                   не задавать вопросы; обязательные токены в окружении
  --skip-apt              не устанавливать apt-пакеты
  --skip-commands         не регистрировать Telegram-команды
  --skip-webhooks         не регистрировать MAX/VK webhook
  --no-start              не запускать сервис
  --status                показать состояние
  -h, --help              справка

Обязательные переменные для --yes:
  TELEGRAM_BOT_TOKEN
  DADATA_API_KEY          если GEOCODER_PROVIDERS содержит dadata

Опционально для MAX:
  MAX_BOT_TOKEN MAX_WEBHOOK_URL MAX_WEBHOOK_SECRET

Опционально для VK:
  VK_BOT_TOKEN VK_GROUP_ID VK_CALLBACK_URL VK_CALLBACK_SECRET VK_CONFIRMATION_CODE
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --skip-commands) SKIP_COMMANDS=1; shift ;;
    --skip-webhooks) SKIP_WEBHOOKS=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --status) STATUS_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Неизвестная опция: $1" ;;
  esac
done

ENV_FILE="$INSTALL_DIR/.env"
VENV_DIR="$INSTALL_DIR/.venv"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
STATE_FILE="$INSTALL_DIR/.install-state"

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
ask_default() {
  local prompt="$1" default="$2" value=""
  if [[ "$ASSUME_YES" -eq 1 ]]; then printf '%s\n' "$default"; return; fi
  read -r -p "$prompt [$default]: " value
  printf '%s\n' "${value:-$default}"
}
mask_token() {
  local value="${1:-}"
  if [[ -z "$value" ]]; then printf 'не задан'; elif [[ ${#value} -le 10 ]]; then printf '***'; else printf '%s…%s' "${value:0:5}" "${value: -4}"; fi
}
get_env_value() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 0
  grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -n 1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true
}
set_env_value() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if [[ -f "$ENV_FILE" ]]; then grep -Ev "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" >"$tmp" || true; fi
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  run_root install -m 0640 -o root -g "$SERVICE_USER" "$tmp" "$ENV_FILE"
  rm -f "$tmp"
}
ensure_env_default() {
  local key="$1" value="$2"
  [[ -n "$(get_env_value "$key" "$ENV_FILE")" ]] || set_env_value "$key" "$value"
}
copy_env_if_set() {
  local key="$1" value="${!1:-}"
  [[ -z "$value" ]] || set_env_value "$key" "$value"
}
providers_require_dadata() { [[ ",$1," == *,dadata,* ]]; }
runtime_enabled() {
  case "$(get_env_value MESSENGER_RUNTIME_ENABLED "$ENV_FILE" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|да) return 0 ;;
    *) return 1 ;;
  esac
}
ask_secret() {
  local key="$1" description="$2" existing env_value value
  env_value="${!key:-}"
  existing="$(get_env_value "$key" "$ENV_FILE")"
  if [[ -n "$env_value" ]]; then success "$key взят из окружения: $(mask_token "$env_value")"; printf '%s\n' "$env_value"; return; fi
  if [[ -n "$existing" ]]; then success "$key найден в $ENV_FILE: $(mask_token "$existing")"; printf '%s\n' "$existing"; return; fi
  [[ "$ASSUME_YES" -eq 0 ]] || fail "$key не задан. Для --yes передайте его переменной окружения."
  while true; do
    read -r -s -p "$description: " value; printf '\n' >&2
    [[ -n "$value" ]] || { warn "Значение пустое"; continue; }
    printf '%s\n' "$value"; return
  done
}
run_with_env() {
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" bash -c 'set -a; source "$1"; set +a; cd "$2"; shift 2; exec "$@"' _ "$ENV_FILE" "$INSTALL_DIR" "$@"
}

print_status() {
  section "Состояние"
  [[ -d "$INSTALL_DIR" ]] && success "Каталог: $INSTALL_DIR" || warn "Каталог отсутствует: $INSTALL_DIR"
  [[ -x "$VENV_DIR/bin/python" ]] && success "venv: $VENV_DIR" || warn "venv отсутствует"
  if [[ -f "$ENV_FILE" ]]; then
    success ".env: $ENV_FILE"
    echo "  MESSENGER_RUNTIME_ENABLED=$(get_env_value MESSENGER_RUNTIME_ENABLED "$ENV_FILE")" >&2
    echo "  TELEGRAM_BOT_TOKEN=$(mask_token "$(get_env_value TELEGRAM_BOT_TOKEN "$ENV_FILE")")" >&2
    echo "  MAX_BOT_TOKEN=$(mask_token "$(get_env_value MAX_BOT_TOKEN "$ENV_FILE")")" >&2
    echo "  VK_BOT_TOKEN=$(mask_token "$(get_env_value VK_BOT_TOKEN "$ENV_FILE")")" >&2
    echo "  GEOCODER_PROVIDERS=$(get_env_value GEOCODER_PROVIDERS "$ENV_FILE")" >&2
    echo "  DADATA_API_KEY=$(mask_token "$(get_env_value DADATA_API_KEY "$ENV_FILE")")" >&2
  else
    warn ".env отсутствует"
  fi
  [[ -f "$UNIT_PATH" ]] && success "systemd unit: $UNIT_PATH" || warn "systemd unit отсутствует"
  if command -v systemctl >/dev/null 2>&1; then systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null || true; fi
}

require_repo_files() {
  local file
  for file in messenger_launcher.py messenger_runtime.py messenger_config_check.py register_messenger_webhooks.py telegram_bot.py requirements.txt runtime_check.py geocoder_preflight.py prepare_basemap_cache.py register_telegram_commands.py .env.telegram.example; do
    [[ -f "$REPO_ROOT/$file" ]] || fail "Не найден $file в $REPO_ROOT"
  done
}
install_system_packages() {
  [[ "$SKIP_APT" -eq 0 ]] || { warn "apt пропущен"; return 0; }
  command -v apt-get >/dev/null 2>&1 || { warn "apt-get не найден"; return 0; }
  confirm "Установить системные пакеты Python, rsync, ffmpeg и eccodes?" || return 0
  run_root apt-get update
  run_root apt-get install -y python3 python3-venv python3-pip ca-certificates rsync fonts-dejavu-core fonts-dejavu-extra ffmpeg
  run_root apt-get install -y python3-dev build-essential pkg-config libeccodes0 libeccodes-dev || warn "Дополнительные GRIB-пакеты установлены не полностью"
  run_root apt-get install -y --no-install-recommends fonts-liberation || warn "Шрифты Liberation установлены не полностью"
  return 0
}
ensure_service_user() {
  id "$SERVICE_USER" >/dev/null 2>&1 && return 0
  run_root useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
}
copy_project() {
  log "Копирую проект в $INSTALL_DIR"
  run_root mkdir -p "$INSTALL_DIR"
  run_root rsync -a --delete \
    --exclude '.git/' --exclude '.venv/' --exclude '.cache_gfs/' --exclude 'data/basemap/' \
    --exclude '.env' --exclude '.install-state' --exclude '__pycache__/' --exclude '*.pyc' \
    "$REPO_ROOT/" "$INSTALL_DIR/"
  run_root chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}
create_venv() {
  [[ -x "$VENV_DIR/bin/python" ]] || run_user "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --prefer-binary -r "$INSTALL_DIR/requirements.txt"
}
configure_env() {
  local providers telegram_token dadata_token key
  providers="${GEOCODER_PROVIDERS:-$(get_env_value GEOCODER_PROVIDERS "$ENV_FILE")}"
  providers="${providers:-$DEFAULT_GEOCODER_PROVIDERS}"
  telegram_token="$(ask_secret TELEGRAM_BOT_TOKEN 'Введите TELEGRAM_BOT_TOKEN')"
  dadata_token=""
  if providers_require_dadata "$providers"; then
    dadata_token="$(ask_secret DADATA_API_KEY 'Введите API-ключ DaData (Secret Key не нужен)')"
  fi

  [[ -f "$ENV_FILE" ]] || run_root install -m 0640 -o root -g "$SERVICE_USER" "$INSTALL_DIR/.env.telegram.example" "$ENV_FILE"
  set_env_value TELEGRAM_BOT_TOKEN "$telegram_token"
  set_env_value GEOCODER_PROVIDERS "$providers"
  [[ -z "$dadata_token" ]] || set_env_value DADATA_API_KEY "$dadata_token"

  for key in MAX_BOT_TOKEN MAX_WEBHOOK_URL MAX_WEBHOOK_SECRET VK_BOT_TOKEN VK_GROUP_ID VK_CALLBACK_URL VK_CALLBACK_SECRET VK_CONFIRMATION_CODE VK_API_VERSION MESSENGER_RUNTIME_ENABLED MESSENGER_RUNTIME_HOST MESSENGER_RUNTIME_PORT MESSENGER_RUNTIME_LOG_LEVEL MESSENGER_RUNTIME_ACCESS_LOG MESSENGER_PREFERENCES_DB TELEGRAM_PREFERENCES_DB; do
    copy_env_if_set "$key"
  done

  ensure_env_default MESSENGER_RUNTIME_ENABLED "1"
  ensure_env_default MESSENGER_RUNTIME_HOST "127.0.0.1"
  ensure_env_default MESSENGER_RUNTIME_PORT "8081"
  ensure_env_default MESSENGER_RUNTIME_LOG_LEVEL "info"
  ensure_env_default MESSENGER_RUNTIME_ACCESS_LOG "0"
  ensure_env_default MESSENGER_PREFERENCES_DB ".cache_gfs/messenger_preferences.sqlite3"
  ensure_env_default TELEGRAM_PREFERENCES_DB ".cache_gfs/telegram_preferences.sqlite3"
  ensure_env_default DADATA_SUGGEST_URL "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
  ensure_env_default DADATA_TIMEOUT "12"
  ensure_env_default NOMINATIM_URL "https://nominatim.openstreetmap.org/search"
  ensure_env_default GEOCODER_USER_AGENT "gfs-profile-messenger-bot/0.1"
  ensure_env_default GEOCODE_CACHE_DIR ".cache_gfs/geocode"
  ensure_env_default GEOCODE_CACHE_TTL_SECONDS "2592000"
  ensure_env_default GEOCODE_TIMEOUT "12"
  ensure_env_default DEFAULT_LEAD "24"
  ensure_env_default MAX_CONCURRENT_GFS "2"
  ensure_env_default MAX_CONCURRENT_GEOCODE "2"
  ensure_env_default MAX_CONCURRENT_METEOGRAM "2"
  ensure_env_default GFS_CACHE_DIR ".cache_gfs"
  ensure_env_default GFS_CACHE_TTL_SECONDS "86400"
  ensure_env_default GFS_AVAILABILITY_CACHE_TTL_SECONDS "300"
  ensure_env_default GFS_REQUEST_TIMEOUT "35"
  ensure_env_default MAP_BASEMAP_DIR "$INSTALL_DIR/data/basemap"
  ensure_env_default MAP_BASEMAP_RESOLUTION "10m"
  ensure_env_default MAP_BASEMAP_AUTO_DOWNLOAD "1"
  ensure_env_default MAP_BASEMAP_DOWNLOAD_TIMEOUT "30"
  ensure_env_default MAP_ANIMATION_PIXEL_SIZE "1280"
  ensure_env_default MAP_ANIMATION_FRAME_DURATION_MS "650"
  ensure_env_default MAP_ANIMATION_OUTPUT_FPS "8"
  ensure_env_default MAP_ANIMATION_CRF "20"
  ensure_env_default MPLBACKEND "Agg"
  ensure_env_default PYTHONUNBUFFERED "1"
  run_root chmod 0640 "$ENV_FILE"
  run_root chown root:"$SERVICE_USER" "$ENV_FILE"
}
validate_runtime() {
  log "Проверяю импорты"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/runtime_check.py"
  log "Проверяю messenger env без сетевых изменений"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/messenger_config_check.py"
  log "Проверяю DaData контрольным запросом"
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/geocoder_preflight.py"
  bash -n "$INSTALL_DIR/install_telegram_bot.sh"
  bash -n "$INSTALL_DIR/deploy_telegram_bot.sh"
}
prepare_basemap_cache() {
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/prepare_basemap_cache.py" --resolution "$(get_env_value MAP_BASEMAP_RESOLUTION "$ENV_FILE")" || warn "Не удалось подготовить basemap"
  return 0
}
write_service() {
  cat <<EOF | run_root tee "$UNIT_PATH" >/dev/null
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
ReadWritePaths=$INSTALL_DIR$EXTRA_READ_WRITE_PATHS

[Install]
WantedBy=multi-user.target
EOF
  run_root chmod 0644 "$UNIT_PATH"
  run_root systemctl daemon-reload
}
start_service() {
  [[ "$NO_START" -eq 0 ]] || { warn "Запуск пропущен"; return 0; }
  run_root systemctl enable --now "${SERVICE_NAME}.service"
  sleep 2
  systemctl is-active --quiet "${SERVICE_NAME}.service" || { run_root journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true; fail "Сервис не стартовал"; }
}
wait_runtime_ready() {
  [[ "$NO_START" -eq 0 ]] || return 0
  runtime_enabled || return 0
  local host port url attempt
  host="$(get_env_value MESSENGER_RUNTIME_HOST "$ENV_FILE")"; host="${host:-127.0.0.1}"
  port="$(get_env_value MESSENGER_RUNTIME_PORT "$ENV_FILE")"; port="${port:-8081}"
  [[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
  url="http://${host}:${port}/ready"
  for attempt in {1..20}; do
    if run_with_env "$VENV_DIR/bin/python" -c 'import requests,sys; r=requests.get(sys.argv[1], timeout=2); raise SystemExit(0 if r.status_code == 200 else 1)' "$url" >/dev/null 2>&1; then
      success "Runtime ready: $url"
      return 0
    fi
    sleep 1
  done
  run_root journalctl -u "${SERVICE_NAME}.service" -n 100 --no-pager || true
  fail "Multi-messenger runtime не прошёл /ready: $url"
}
register_commands() {
  [[ "$SKIP_COMMANDS" -eq 0 ]] || return 0
  run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/register_telegram_commands.py" || warn "Не удалось зарегистрировать Telegram-команды"
  return 0
}
register_webhooks() {
  [[ "$SKIP_WEBHOOKS" -eq 0 ]] || { warn "Регистрация MAX/VK webhook пропущена"; return 0; }
  runtime_enabled || return 0
  if run_with_env "$VENV_DIR/bin/python" "$INSTALL_DIR/register_messenger_webhooks.py"; then
    success "MAX/VK webhook проверены и зарегистрированы"
    return 0
  fi
  fail "Runtime работает, но регистрация MAX/VK webhook не прошла; проверьте публичный HTTPS и env"
}
write_state() {
  cat <<EOF | run_root tee "$STATE_FILE" >/dev/null
installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
install_dir=$INSTALL_DIR
service_name=$SERVICE_NAME
service_user=$SERVICE_USER
venv=$VENV_DIR
unit=$UNIT_PATH
entrypoint=messenger_launcher.py
messenger_runtime=$(get_env_value MESSENGER_RUNTIME_ENABLED "$ENV_FILE")
max_configured=$([[ -n "$(get_env_value MAX_BOT_TOKEN "$ENV_FILE")" ]] && printf yes || printf no)
vk_configured=$([[ -n "$(get_env_value VK_BOT_TOKEN "$ENV_FILE")" ]] && printf yes || printf no)
geocoder_providers=$(get_env_value GEOCODER_PROVIDERS "$ENV_FILE")
dadata=validated
runtime_check=ok
EOF
  run_root chown "$SERVICE_USER:$SERVICE_USER" "$STATE_FILE"
}

main() {
  section "$APP_NAME"
  print_status
  [[ "$STATUS_ONLY" -eq 0 ]] || exit 0
  require_repo_files
  INSTALL_DIR="$(ask_default 'Каталог установки' "$INSTALL_DIR")"
  SERVICE_NAME="$(ask_default 'Имя systemd-сервиса' "$SERVICE_NAME")"
  SERVICE_USER="$(ask_default 'Пользователь сервиса' "$SERVICE_USER")"
  ENV_FILE="$INSTALL_DIR/.env"; VENV_DIR="$INSTALL_DIR/.venv"; UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"; STATE_FILE="$INSTALL_DIR/.install-state"
  confirm "Установить $APP_NAME в $INSTALL_DIR?" || fail "Отменено"
  install_system_packages
  ensure_service_user
  copy_project
  create_venv
  configure_env
  validate_runtime
  prepare_basemap_cache
  write_service
  start_service
  wait_runtime_ready
  register_commands
  register_webhooks
  write_state
  success "Установка завершена: Telegram + общий messenger runtime готовы."
}

main "$@"
