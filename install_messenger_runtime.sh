#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/gfs_profile}"
SERVICE_NAME="${SERVICE_NAME:-gfs-profile-bot}"
SERVICE_USER="${SERVICE_USER:-gfsbot}"
ENV_FILE="$INSTALL_DIR/.env"
VENV_DIR="$INSTALL_DIR/.venv"
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
DROPIN_PATH="$DROPIN_DIR/10-messenger-runtime.conf"
ACTION="status"
NO_RESTART=0
REGISTER=1

log() { printf '[messenger-runtime] %s\n' "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

usage() {
  cat <<'USAGE'
Использование:
  sudo bash install_messenger_runtime.sh --status
  sudo bash install_messenger_runtime.sh --enable
  sudo bash install_messenger_runtime.sh --disable

Опции:
  --enable       включить single-process Telegram+MAX+VK runtime
  --disable      оставить launcher, но вернуть Telegram-only polling
  --status       показать состояние
  --no-register  не регистрировать MAX/VK webhook после запуска
  --no-restart   не перезапускать systemd service
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable) ACTION="enable"; shift ;;
    --disable) ACTION="disable"; shift ;;
    --status) ACTION="status"; shift ;;
    --no-register) REGISTER=0; shift ;;
    --no-restart) NO_RESTART=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Неизвестная опция: $1" ;;
  esac
done

if [[ "$NO_RESTART" -eq 1 && "$REGISTER" -eq 1 && "$ACTION" == "enable" ]]; then
  fail "--no-restart при --enable требует --no-register: регистрировать webhook до запуска endpoint нельзя"
fi

[[ "$(id -u)" -eq 0 ]] || fail "Нужен root: sudo bash install_messenger_runtime.sh ..."

read_env() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true
}

set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -Ev "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  install -m 0640 -o root -g "$SERVICE_USER" "$tmp" "$ENV_FILE"
  rm -f "$tmp"
}

validate_install() {
  [[ -f "$ENV_FILE" ]] || fail "Нет $ENV_FILE"
  [[ -x "$VENV_DIR/bin/python" ]] || fail "Нет $VENV_DIR/bin/python"
  [[ -f "$INSTALL_DIR/messenger_launcher.py" ]] || fail "Нет messenger_launcher.py — сначала обновите telegram-bot"
  [[ -f "$INSTALL_DIR/register_messenger_webhooks.py" ]] || fail "Нет register_messenger_webhooks.py — сначала обновите telegram-bot"
  id "$SERVICE_USER" >/dev/null 2>&1 || fail "Нет пользователя $SERVICE_USER"
}

validate_platform_env() {
  local max_token vk_token
  max_token="$(read_env MAX_BOT_TOKEN)"
  vk_token="$(read_env VK_BOT_TOKEN)"
  [[ -n "$max_token" || -n "$vk_token" ]] || fail "Для --enable задайте MAX_BOT_TOKEN и/или VK_BOT_TOKEN"
  if [[ -n "$max_token" ]]; then
    [[ "$(read_env MAX_WEBHOOK_URL)" == https://* ]] || fail "MAX_WEBHOOK_URL должен быть HTTPS"
    [[ -n "$(read_env MAX_WEBHOOK_SECRET)" ]] || fail "MAX_WEBHOOK_SECRET не задан"
  fi
  if [[ -n "$vk_token" ]]; then
    [[ "$(read_env VK_CALLBACK_URL)" == https://* ]] || fail "VK_CALLBACK_URL должен быть HTTPS"
    [[ "$(read_env VK_GROUP_ID)" =~ ^[0-9]+$ ]] || fail "VK_GROUP_ID должен быть числом"
    [[ -n "$(read_env VK_CALLBACK_SECRET)" ]] || fail "VK_CALLBACK_SECRET не задан"
    [[ -n "$(read_env VK_CONFIRMATION_CODE)" ]] || fail "VK_CONFIRMATION_CODE не задан"
  fi
}

install_dropin() {
  mkdir -p "$DROPIN_DIR"
  cat >"$DROPIN_PATH" <<EOF2
[Service]
ExecStart=
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/messenger_launcher.py
EOF2
  chmod 0644 "$DROPIN_PATH"
}

restart_service() {
  systemctl daemon-reload
  [[ "$NO_RESTART" -eq 0 ]] || return 0
  systemctl restart "${SERVICE_NAME}.service" || return 1
  if ! systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true
    return 1
  fi
}

run_python_env() {
  runuser -u "$SERVICE_USER" -- env HOME="$INSTALL_DIR" bash -c '
    set -a
    source "$1"
    set +a
    cd "$2"
    shift 2
    exec "$@"
  ' _ "$ENV_FILE" "$INSTALL_DIR" "$@"
}

run_registration() {
  [[ "$REGISTER" -eq 1 ]] || return 0
  run_python_env "$VENV_DIR/bin/python" "$INSTALL_DIR/register_messenger_webhooks.py"
}

show_status() {
  echo "service=$SERVICE_NAME"
  echo "dropin=$([[ -f "$DROPIN_PATH" ]] && echo installed || echo absent)"
  echo "runtime_enabled=${MESSENGER_RUNTIME_ENABLED:-$(read_env MESSENGER_RUNTIME_ENABLED)}"
  echo "max_configured=$([[ -n "$(read_env MAX_BOT_TOKEN)" ]] && echo yes || echo no)"
  echo "vk_configured=$([[ -n "$(read_env VK_BOT_TOKEN)" ]] && echo yes || echo no)"
  systemctl is-active "${SERVICE_NAME}.service" 2>/dev/null || true
}

validate_install
case "$ACTION" in
  status)
    show_status
    ;;
  disable)
    install_dropin
    set_env MESSENGER_RUNTIME_ENABLED 0
    restart_service || fail "Сервис не стартовал в Telegram-only режиме"
    show_status
    ;;
  enable)
    validate_platform_env
    install_dropin
    set_env MESSENGER_RUNTIME_ENABLED 1
    if ! run_python_env "$VENV_DIR/bin/python" "$INSTALL_DIR/runtime_check.py"; then
      set_env MESSENGER_RUNTIME_ENABLED 0
      fail "runtime_check не пройден; multi-messenger runtime отключён"
    fi
    if ! restart_service; then
      set_env MESSENGER_RUNTIME_ENABLED 0
      systemctl restart "${SERVICE_NAME}.service" || true
      fail "Не удалось включить runtime; возвращён Telegram-only режим"
    fi
    if ! run_registration; then
      fail "Runtime запущен, но регистрация webhook не прошла. Исправьте HTTPS/env и повторите регистрацию; endpoint оставлен активным, чтобы не создавать недоступные частичные подписки."
    fi
    show_status
    ;;
esac
