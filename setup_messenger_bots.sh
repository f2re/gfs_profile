#!/usr/bin/env bash
# One-time interactive/non-interactive MAX/VK setup for an existing GFS Profile install.

set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/gfs_profile}"
SERVICE_USER="${SERVICE_USER:-gfsbot}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$INSTALL_DIR/.env"
VENV_PY="$INSTALL_DIR/.venv/bin/python"
ASSUME_YES=0
STATUS_ONLY=0
SETUP_MAX=0
SETUP_VK=0

log() { printf '▶ %s\n' "$*" >&2; }
ok() { printf '✓ %s\n' "$*" >&2; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Настройка MAX/VK для GFS Profile

Сначала создайте бота MAX и/или сообщество VK по docs/MESSENGER_REGISTRATION.md.
Затем запустите этот скрипт из checkout ветки telegram-bot.

Использование:
  sudo bash setup_messenger_bots.sh [--max] [--vk] [--yes] [--status]

Если --max/--vk не указаны, в интерактивном режиме будут предложены обе платформы.
Для --yes передайте нужные значения через окружение:

MAX:
  MAX_BOT_TOKEN
  MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max

VK:
  VK_BOT_TOKEN
  VK_GROUP_ID=123456789
  VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk

MAX_WEBHOOK_SECRET, VK_CALLBACK_SECRET и VK_CONFIRMATION_CODE можно не задавать:
они будут подготовлены автоматически.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --service-user) SERVICE_USER="$2"; shift 2 ;;
    --max) SETUP_MAX=1; shift ;;
    --vk) SETUP_VK=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --status) STATUS_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Неизвестная опция: $1" ;;
  esac
done

ENV_FILE="$INSTALL_DIR/.env"
VENV_PY="$INSTALL_DIR/.venv/bin/python"
[[ "$(id -u)" -eq 0 ]] || fail "Запустите через sudo: sudo bash setup_messenger_bots.sh"
[[ -f "$ENV_FILE" ]] || fail "Нет $ENV_FILE. Сначала установите бот: bash install_telegram_bot.sh"
[[ -x "$VENV_PY" ]] || fail "Нет $VENV_PY. Сначала выполните базовую установку."

read_env() {
  local key="$1"
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
ask_value() {
  local key="$1" prompt="$2" secret="${3:-0}" value=""
  value="${!key:-}"
  [[ -n "$value" ]] || value="$(read_env "$key")"
  if [[ -n "$value" ]]; then printf '%s' "$value"; return 0; fi
  [[ "$ASSUME_YES" -eq 0 ]] || fail "$key не задан для --yes"
  if [[ "$secret" -eq 1 ]]; then read -r -s -p "$prompt: " value; printf '\n' >&2; else read -r -p "$prompt: " value; fi
  [[ -n "$value" ]] || fail "$key не может быть пустым"
  printf '%s' "$value"
}
ask_yes() {
  local prompt="$1" answer=""
  [[ "$ASSUME_YES" -eq 0 ]] || return 0
  read -r -p "$prompt [y/N]: " answer
  [[ "$answer" =~ ^[YyДд]$ ]]
}
run_installed_env() {
  env HOME="$INSTALL_DIR" bash -c 'set -a; source "$1"; set +a; cd "$2"; shift 2; exec "$@"' _ "$ENV_FILE" "$INSTALL_DIR" "$@"
}

if [[ "$STATUS_ONLY" -eq 1 ]]; then
  run_installed_env "$VENV_PY" "$INSTALL_DIR/register_messenger_webhooks.py" --status
  exit $?
fi

if [[ "$SETUP_MAX" -eq 0 && "$SETUP_VK" -eq 0 ]]; then
  ask_yes "Настроить MAX?" && SETUP_MAX=1 || true
  ask_yes "Настроить VK?" && SETUP_VK=1 || true
fi
[[ "$SETUP_MAX" -eq 1 || "$SETUP_VK" -eq 1 ]] || fail "Не выбрана ни одна платформа"

if [[ "$SETUP_MAX" -eq 1 ]]; then
  MAX_BOT_TOKEN_VALUE="$(ask_value MAX_BOT_TOKEN 'MAX token из Расширенные настройки → Настроить' 1)"
  MAX_WEBHOOK_URL_VALUE="$(ask_value MAX_WEBHOOK_URL 'Публичный HTTPS URL MAX webhook, например https://bot.example.ru/webhooks/max')"
  set_env MAX_BOT_TOKEN "$MAX_BOT_TOKEN_VALUE"
  set_env MAX_WEBHOOK_URL "$MAX_WEBHOOK_URL_VALUE"
  [[ -n "${MAX_WEBHOOK_SECRET:-}" ]] && set_env MAX_WEBHOOK_SECRET "$MAX_WEBHOOK_SECRET"
  ok "MAX token/URL записаны; secret будет сгенерирован при необходимости"
fi

if [[ "$SETUP_VK" -eq 1 ]]; then
  VK_BOT_TOKEN_VALUE="$(ask_value VK_BOT_TOKEN 'VK community access token' 1)"
  VK_GROUP_ID_VALUE="$(ask_value VK_GROUP_ID 'Числовой VK group_id без минуса')"
  VK_CALLBACK_URL_VALUE="$(ask_value VK_CALLBACK_URL 'Публичный HTTPS URL VK Callback API, например https://bot.example.ru/webhooks/vk')"
  VK_API_VERSION_VALUE="${VK_API_VERSION:-$(read_env VK_API_VERSION)}"
  VK_API_VERSION_VALUE="${VK_API_VERSION_VALUE:-5.199}"
  set_env VK_BOT_TOKEN "$VK_BOT_TOKEN_VALUE"
  set_env VK_GROUP_ID "$VK_GROUP_ID_VALUE"
  set_env VK_CALLBACK_URL "$VK_CALLBACK_URL_VALUE"
  set_env VK_API_VERSION "$VK_API_VERSION_VALUE"
  [[ -n "${VK_CALLBACK_SECRET:-}" ]] && set_env VK_CALLBACK_SECRET "$VK_CALLBACK_SECRET"
  [[ -n "${VK_CONFIRMATION_CODE:-}" ]] && set_env VK_CONFIRMATION_CODE "$VK_CONFIRMATION_CODE"
  ok "VK token/group/URL записаны; secret и confirmation code будут подготовлены автоматически"
fi

log "Подготавливаю secrets и VK confirmation code"
"$VENV_PY" "$SCRIPT_DIR/prepare_messenger_config.py" --env-file "$ENV_FILE"
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

log "Проверяю конфигурацию до deploy"
run_installed_env "$VENV_PY" "$SCRIPT_DIR/messenger_config_check.py"

log "Запускаю штатный deploy; после /ready он зарегистрирует MAX/VK webhook"
bash "$SCRIPT_DIR/deploy_telegram_bot.sh" --install-dir "$INSTALL_DIR" --service-user "$SERVICE_USER" --yes

log "Проверяю фактическую регистрацию"
run_installed_env "$VENV_PY" "$INSTALL_DIR/register_messenger_webhooks.py" --status
ok "MAX/VK setup завершён"
