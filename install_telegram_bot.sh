#!/usr/bin/env bash
# Полная установка Telegram-бота GFS Profile.
# Скрипт копирует проект в /opt, создаёт venv, .env, systemd-сервис и запускает бота.

set -Eeuo pipefail

APP_NAME="GFS Profile Bot"
DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_SERVICE_USER="gfsbot"
DEFAULT_DEFAULT_LEAD="24"
DEFAULT_MAX_CONCURRENT_GFS="2"
DEFAULT_CACHE_TTL="86400"
DEFAULT_REQUEST_TIMEOUT="35"
DEFAULT_GEOCODE_TIMEOUT="12"

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
SERVICE_USER="${SERVICE_USER:-$DEFAULT_SERVICE_USER}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ASSUME_YES=0
SKIP_APT=0
NO_START=0
STATUS_ONLY=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

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
$APP_NAME — установка Telegram-бота

Использование:
  ./install_telegram_bot.sh [опции]

Опции:
  --install-dir DIR       каталог установки, по умолчанию $DEFAULT_INSTALL_DIR
  --service-name NAME     имя systemd-сервиса, по умолчанию $DEFAULT_SERVICE_NAME
  --service-user USER     системный пользователь, по умолчанию $DEFAULT_SERVICE_USER
  --python PATH           Python-интерпретатор, по умолчанию python3
  --yes                   не задавать подтверждающие вопросы, брать значения по умолчанию
  --skip-apt              не устанавливать системные пакеты через apt
  --no-start              создать сервис, но не запускать
  --status                только показать состояние установки
  -h, --help              показать справку

Переменные окружения можно задать заранее:
  TELEGRAM_BOT_TOKEN, DEFAULT_LEAD, MAX_CONCURRENT_GFS, GFS_CACHE_DIR,
  GFS_CACHE_TTL_SECONDS, GFS_REQUEST_TIMEOUT, GEOCODER_USER_AGENT,
  GEOCODE_CACHE_DIR, GEOCODE_TIMEOUT
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

ask_default() {
  local prompt="$1" default="$2" value=""
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    printf '%s\n' "$default"
    return 0
  fi
  read -r -p "$(printf '%s [%s]: ' "$prompt" "$default")" value
  printf '%s\n' "${value:-$default}"
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

mask_token() {
  local token="${1:-}"
  if [[ -z "$token" ]]; then
    printf 'не задан'
  elif [[ ${#token} -le 12 ]]; then
    printf '***'
  else
    printf '%s…%s' "${token:0:6}" "${token: -4}"
  fi
}

get_env_value() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 0
  grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true
}

print_status() {
  section "Состояние"
  if [[ -d "$INSTALL_DIR" ]]; then success "Каталог установки: $INSTALL_DIR"; else warn "Каталог установки отсутствует: $INSTALL_DIR"; fi
  if id "$SERVICE_USER" >/dev/null 2>&1; then success "Пользователь сервиса: $SERVICE_USER"; else warn "Пользователь сервиса отсутствует: $SERVICE_USER"; fi
  if [[ -x "$VENV_DIR/bin/python" ]]; then success "Python venv: $VENV_DIR"; else warn "Python venv не найден: $VENV_DIR"; fi
  if [[ -f "$ENV_FILE" ]]; then
    local token
    token="$(get_env_value TELEGRAM_BOT_TOKEN "$ENV_FILE")"
    success ".env найден: $ENV_FILE, токен: $(mask_token "$token")"
  else
    warn ".env не найден: $ENV_FILE"
  fi
  if [[ -f "$UNIT_PATH" ]]; then success "systemd unit: $UNIT_PATH"; else warn "systemd unit отсутствует: $UNIT_PATH"; fi
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
      printf 'Активность сервиса: ' >&2
      systemctl is-active "${SERVICE_NAME}.service" >&2 || true
      printf 'Автозапуск: ' >&2
      systemctl is-enabled "${SERVICE_NAME}.service" >&2 || true
    fi
  fi
  if [[ -f "$STATE_FILE" ]]; then
    echo "Состояние установки:" >&2
    sed 's/^/  /' "$STATE_FILE" >&2 || true
  fi
}

require_repo_files() {
  [[ -f "$REPO_ROOT/telegram_bot.py" ]] || fail "Не найден telegram_bot.py. Запускайте скрипт из корня репозитория или из файла в корне проекта."
  [[ -f "$REPO_ROOT/requirements.txt" ]] || fail "Не найден requirements.txt."
  [[ -f "$REPO_ROOT/gfs_core.py" ]] || fail "Не найден gfs_core.py."
}

install_system_packages() {
  [[ "$SKIP_APT" -eq 1 ]] && { warn "Установка системных пакетов пропущена (--skip-apt)"; return 0; }
  command -v apt-get >/dev/null 2>&1 || { warn "apt-get не найден. Пропускаю системные пакеты."; return 0; }
  confirm "Установить/обновить системные пакеты python3-venv, python3-pip, ca-certificates?" || return 0
  log "Обновляю apt и ставлю базовые пакеты"
  run_root apt-get update
  run_root apt-get install -y python3 python3-venv python3-pip ca-certificates
}

ensure_service_user() {
  if id "$SERVICE_USER" >/dev/null 2>&1; then
    success "Пользователь $SERVICE_USER уже существует"
    return 0
  fi
  log "Создаю системного пользователя $SERVICE_USER"
  run_root useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
}

copy_project() {
  log "Копирую проект в $INSTALL_DIR"
  run_root mkdir -p "$INSTALL_DIR"
  if command -v rsync >/dev/null 2>&1; then
    run_root rsync -a --delete \
      --exclude '.git/' \
      --exclude '.venv/' \
      --exclude '.cache_gfs/' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      "$REPO_ROOT/" "$INSTALL_DIR/"
  else
    warn "rsync не найден. Использую tar-copy без удаления старых лишних файлов."
    (cd "$REPO_ROOT" && tar \
      --exclude='./.git' \
      --exclude='./.venv' \
      --exclude='./.cache_gfs' \
      --exclude='*/__pycache__' \
      --exclude='*.pyc' \
      -cf - .) | run_root tar -xf - -C "$INSTALL_DIR"
  fi
  run_root chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}

create_venv() {
  log "Создаю Python-окружение"
  run_user "$SERVICE_USER" "$PYTHON_BIN" -m venv "$VENV_DIR"
  log "Обновляю pip и ставлю зависимости"
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install --upgrade pip
  run_user "$SERVICE_USER" env HOME="$INSTALL_DIR" "$VENV_DIR/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
}

ask_token() {
  local existing env_token token
  existing="$(get_env_value TELEGRAM_BOT_TOKEN "$ENV_FILE")"
  env_token="${TELEGRAM_BOT_TOKEN:-}"

  if [[ -n "$env_token" ]]; then
    success "TELEGRAM_BOT_TOKEN взят из переменной окружения: $(mask_token "$env_token")"
    printf '%s\n' "$env_token"
    return 0
  fi

  if [[ -n "$existing" ]]; then
    success "TELEGRAM_BOT_TOKEN уже есть в $ENV_FILE: $(mask_token "$existing")"
    if confirm "Оставить существующий токен?"; then
      printf '%s\n' "$existing"
      return 0
    fi
  fi

  if [[ "$ASSUME_YES" -eq 1 ]]; then
    fail "TELEGRAM_BOT_TOKEN не задан. Для --yes задайте токен переменной окружения."
  fi

  while true; do
    read -r -s -p "Введите TELEGRAM_BOT_TOKEN: " token
    printf '\n' >&2
    [[ -n "$token" ]] || { warn "Токен пустой"; continue; }
    [[ "$token" == *:* ]] || warn "Токен обычно содержит двоеточие. Проверьте значение."
    printf '%s\n' "$token"
    return 0
  done
}

write_env() {
  local token default_lead max_concurrent cache_dir ttl timeout ua geocode_dir geocode_timeout
  token="$(ask_token)"
  default_lead="$(ask_default "Срок прогноза по умолчанию, часы" "${DEFAULT_LEAD:-$DEFAULT_DEFAULT_LEAD}")"
  max_concurrent="$(ask_default "Максимум одновременных GFS-запросов" "${MAX_CONCURRENT_GFS:-$DEFAULT_MAX_CONCURRENT_GFS}")"
  cache_dir="$(ask_default "Каталог кэша GRIB2" "${GFS_CACHE_DIR:-.cache_gfs}")"
  ttl="$(ask_default "TTL кэша GRIB2, секунд" "${GFS_CACHE_TTL_SECONDS:-$DEFAULT_CACHE_TTL}")"
  timeout="$(ask_default "Timeout NOMADS, секунд" "${GFS_REQUEST_TIMEOUT:-$DEFAULT_REQUEST_TIMEOUT}")"
  ua="$(ask_default "User-Agent геокодера" "${GEOCODER_USER_AGENT:-gfs-profile-telegram-bot/0.1}")"
  geocode_dir="$(ask_default "Каталог кэша геокодера" "${GEOCODE_CACHE_DIR:-.cache_gfs/geocode}")"
  geocode_timeout="$(ask_default "Timeout геокодера, секунд" "${GEOCODE_TIMEOUT:-$DEFAULT_GEOCODE_TIMEOUT}")"

  log "Записываю $ENV_FILE"
  run_root install -m 0640 -o root -g "$SERVICE_USER" /dev/null "$ENV_FILE"
  cat <<EOF | run_root tee "$ENV_FILE" >/dev/null
TELEGRAM_BOT_TOKEN=$token
DEFAULT_LEAD=$default_lead
MAX_CONCURRENT_GFS=$max_concurrent
GFS_CACHE_DIR=$cache_dir
GFS_CACHE_TTL_SECONDS=$ttl
GFS_REQUEST_TIMEOUT=$timeout
GEOCODER_USER_AGENT=$ua
GEOCODE_CACHE_DIR=$geocode_dir
GEOCODE_TIMEOUT=$geocode_timeout
EOF
  run_root chmod 0640 "$ENV_FILE"
  run_root chown root:"$SERVICE_USER" "$ENV_FILE"

  if [[ "$cache_dir" = /* ]]; then
    run_root mkdir -p "$cache_dir"
    run_root chown -R "$SERVICE_USER:$SERVICE_USER" "$cache_dir"
  else
    run_root mkdir -p "$INSTALL_DIR/$cache_dir"
    run_root chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/$cache_dir"
  fi
}

write_service() {
  log "Создаю systemd-сервис $SERVICE_NAME"
  cat <<EOF | run_root tee "$UNIT_PATH" >/dev/null
[Unit]
Description=GFS Profile Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/telegram_bot.py
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
  run_root chmod 0644 "$UNIT_PATH"
  run_root systemctl daemon-reload
}

write_state() {
  cat <<EOF | run_root tee "$STATE_FILE" >/dev/null
installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
install_dir=$INSTALL_DIR
service_name=$SERVICE_NAME
service_user=$SERVICE_USER
python=$PYTHON_BIN
venv=$VENV_DIR
unit=$UNIT_PATH
EOF
  run_root chown "$SERVICE_USER:$SERVICE_USER" "$STATE_FILE"
}

start_service() {
  if [[ "$NO_START" -eq 1 ]]; then
    warn "Сервис создан, но не запущен (--no-start)"
    return 0
  fi
  log "Включаю и запускаю сервис"
  run_root systemctl enable --now "${SERVICE_NAME}.service"
  sleep 2
  if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    success "Сервис активен: ${SERVICE_NAME}.service"
  else
    warn "Сервис не активен. Последние строки журнала:"
    run_root journalctl -u "${SERVICE_NAME}.service" -n 40 --no-pager || true
    fail "Установка завершилась, но сервис не стартовал"
  fi
}

main() {
  section "$APP_NAME"
  print_status
  [[ "$STATUS_ONLY" -eq 1 ]] && exit 0

  require_repo_files
  INSTALL_DIR="$(ask_default "Каталог установки" "$INSTALL_DIR")"
  SERVICE_NAME="$(ask_default "Имя systemd-сервиса" "$SERVICE_NAME")"
  SERVICE_USER="$(ask_default "Пользователь сервиса" "$SERVICE_USER")"
  PYTHON_BIN="$(ask_default "Python-интерпретатор" "$PYTHON_BIN")"
  ENV_FILE="$INSTALL_DIR/.env"
  VENV_DIR="$INSTALL_DIR/.venv"
  UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
  STATE_FILE="$INSTALL_DIR/.install-state"

  section "План установки"
  echo "Каталог:       $INSTALL_DIR" >&2
  echo "Сервис:        $SERVICE_NAME.service" >&2
  echo "Пользователь:  $SERVICE_USER" >&2
  echo "Python:        $PYTHON_BIN" >&2
  echo "Файл .env:     $ENV_FILE" >&2
  confirm "Продолжить установку?" || fail "Отменено пользователем"

  install_system_packages
  ensure_service_user
  copy_project
  create_venv
  write_env
  write_service
  write_state
  start_service

  section "Готово"
  success "Бот установлен и настроен"
  echo "Проверить состояние:  sudo systemctl status ${SERVICE_NAME}.service"
  echo "Смотреть журнал:      sudo journalctl -u ${SERVICE_NAME}.service -f"
  echo "Повторная проверка:   $INSTALL_DIR/install_telegram_bot.sh --status"
}

main "$@"
