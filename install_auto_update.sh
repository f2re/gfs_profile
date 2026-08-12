#!/usr/bin/env bash
# Установка systemd timer для безопасного автообновления ветки telegram-bot.

set -Eeuo pipefail

DEFAULT_BRANCH="telegram-bot"
DEFAULT_REMOTE="origin"
DEFAULT_INTERVAL="60"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_INSTALL_DIR="/opt/gfs_profile"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
BRANCH="$DEFAULT_BRANCH"
REMOTE="$DEFAULT_REMOTE"
INTERVAL="$DEFAULT_INTERVAL"
SERVICE_NAME="$DEFAULT_SERVICE_NAME"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
ASSUME_YES=0
STATUS_ONLY=0
DISABLE=0

usage() {
  cat <<EOF2
Установка автообновления GFS Profile Bot через systemd timer

Использование:
  sudo bash install_auto_update.sh [опции]

Опции:
  --repo-root DIR       checkout репозитория
  --branch NAME         ветка, по умолчанию telegram-bot
  --remote NAME         git remote, по умолчанию origin
  --interval SEC        интервал проверки, по умолчанию 60
  --service-name NAME   основной systemd service
  --install-dir DIR     каталог приложения, по умолчанию /opt/gfs_profile
  --status              показать состояние
  --disable             отключить и удалить timer/service автообновления
  --yes                 без подтверждения
  -h, --help
EOF2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$(readlink -f "$2")"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --status) STATUS_ONLY=1; shift ;;
    --disable) DISABLE=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Неизвестная опция: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "$INTERVAL" =~ ^[0-9]+$ ]] && (( INTERVAL >= 15 )) || { echo "--interval должен быть целым числом >= 15" >&2; exit 2; }

UNIT_BASE="${SERVICE_NAME}-auto-update"
SERVICE_UNIT="/etc/systemd/system/${UNIT_BASE}.service"
TIMER_UNIT="/etc/systemd/system/${UNIT_BASE}.timer"
STATE_DIR="/var/lib/${SERVICE_NAME}"

status() {
  echo "Auto-update unit: $UNIT_BASE"
  systemctl is-enabled "${UNIT_BASE}.timer" 2>/dev/null || true
  systemctl is-active "${UNIT_BASE}.timer" 2>/dev/null || true
  systemctl list-timers "${UNIT_BASE}.timer" --all --no-pager 2>/dev/null || true
  if [[ -f "$STATE_DIR/auto-update.state" ]]; then
    echo
    cat "$STATE_DIR/auto-update.state"
  fi
}

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Нужен root: sudo bash install_auto_update.sh ..." >&2
  exit 1
fi

if [[ "$STATUS_ONLY" -eq 1 ]]; then
  status
  exit 0
fi

if [[ "$DISABLE" -eq 1 ]]; then
  systemctl disable --now "${UNIT_BASE}.timer" 2>/dev/null || true
  rm -f "$SERVICE_UNIT" "$TIMER_UNIT"
  systemctl daemon-reload
  echo "Автообновление отключено"
  exit 0
fi

[[ -d "$REPO_ROOT/.git" ]] || { echo "$REPO_ROOT не является git checkout" >&2; exit 1; }
[[ -x "$REPO_ROOT/auto_update_telegram_bot.sh" ]] || { echo "Не найден auto_update_telegram_bot.sh" >&2; exit 1; }
[[ -f "$REPO_ROOT/deploy_telegram_bot.sh" ]] || { echo "Не найден deploy_telegram_bot.sh" >&2; exit 1; }
REPO_USER="$(stat -c '%U' "$REPO_ROOT/.git")"
REPO_HOME="$(getent passwd "$REPO_USER" 2>/dev/null | cut -d: -f6 || true)"
REPO_HOME="${REPO_HOME:-/root}"
CURRENT_BRANCH="$(runuser -u "$REPO_USER" -- env HOME="$REPO_HOME" git -C "$REPO_ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || { echo "Checkout должен быть в ветке $BRANCH, сейчас: $CURRENT_BRANCH" >&2; exit 1; }
REMOTE_URL="$(runuser -u "$REPO_USER" -- env HOME="$REPO_HOME" git -C "$REPO_ROOT" remote get-url "$REMOTE" 2>/dev/null || true)"
[[ -n "$REMOTE_URL" ]] || { echo "Не найден git remote '$REMOTE'" >&2; exit 1; }

echo "Проверяю доступ к $REMOTE/$BRANCH от имени $REPO_USER..."
if ! runuser -u "$REPO_USER" -- env HOME="$REPO_HOME" git -C "$REPO_ROOT" ls-remote --exit-code "$REMOTE" "refs/heads/$BRANCH" >/dev/null 2>&1; then
  echo "Не удалось прочитать $REMOTE/$BRANCH без интерактивной сессии." >&2
  echo "Remote: $REMOTE_URL" >&2
  echo "Если SSH использует только ssh-agent, настройте постоянный deploy key или HTTPS fetch URL." >&2
  exit 1
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  echo "Будет установлен ${UNIT_BASE}.timer:"
  echo "  repo:     $REPO_ROOT"
  echo "  user:     $REPO_USER"
  echo "  branch:   $BRANCH"
  echo "  remote:   $REMOTE"
  echo "  interval: ${INTERVAL}s"
  read -r -p "Продолжить? [Y/n]: " answer
  [[ -z "$answer" || "$answer" =~ ^[YyДд]$ ]] || exit 1
fi

mkdir -p "$STATE_DIR"
chmod 0755 "$STATE_DIR"

cat >"$SERVICE_UNIT" <<EOF2
[Unit]
Description=GFS Profile Bot safe auto-update
Wants=network-online.target
After=network-online.target
ConditionPathIsDirectory=$REPO_ROOT/.git

[Service]
Type=oneshot
User=root
Group=root
Environment="AUTO_UPDATE_REPO_ROOT=$REPO_ROOT"
Environment="AUTO_UPDATE_REPO_USER=$REPO_USER"
Environment="AUTO_UPDATE_BRANCH=$BRANCH"
Environment="AUTO_UPDATE_REMOTE=$REMOTE"
Environment="AUTO_UPDATE_STATE_DIR=$STATE_DIR"
Environment="INSTALL_DIR=$INSTALL_DIR"
Environment="SERVICE_NAME=$SERVICE_NAME"
ExecStart=/usr/bin/env bash "$REPO_ROOT/auto_update_telegram_bot.sh"
TimeoutStartSec=45min
Nice=10
IOSchedulingClass=idle
UMask=0022
EOF2

cat >"$TIMER_UNIT" <<EOF2
[Unit]
Description=Check $BRANCH for GFS Profile Bot updates

[Timer]
OnBootSec=30s
OnUnitInactiveSec=${INTERVAL}s
AccuracySec=5s
RandomizedDelaySec=5s
Persistent=true
Unit=${UNIT_BASE}.service

[Install]
WantedBy=timers.target
EOF2

chmod 0644 "$SERVICE_UNIT" "$TIMER_UNIT"
systemctl daemon-reload
systemctl enable --now "${UNIT_BASE}.timer"

# Немедленная контрольная проверка: показывает ошибки конфигурации сразу,
# а не через минуту. Ошибка не удаляет timer — следующий tick повторит fetch.
if ! systemctl start "${UNIT_BASE}.service"; then
  echo "Первый auto-update check завершился ошибкой. Timer оставлен включённым." >&2
  journalctl -u "${UNIT_BASE}.service" -n 80 --no-pager >&2 || true
  exit 1
fi

status
