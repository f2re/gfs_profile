#!/usr/bin/env bash
# Опциональные локальные hooks: ускоряют deploy после ручного git pull/rebase.
# ВАЖНО: hooks сами не узнают о новом commit на GitHub. Для remote monitoring
# используйте install_auto_update.sh (systemd timer).

set -Eeuo pipefail

DEFAULT_INSTALL_DIR="/opt/gfs_profile"
DEFAULT_SERVICE_NAME="gfs-profile-bot"
DEFAULT_BRANCH="telegram-bot"

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
BRANCH="${AUTO_UPDATE_BRANCH:-$DEFAULT_BRANCH}"
ASSUME_YES=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
LOG_FILE="$REPO_ROOT/.git/gfs-profile-deploy.log"

usage() {
  cat <<EOF2
Локальные git hooks для deploy после ручного git pull/rebase

Использование:
  ./install_git_hooks.sh [--yes]

Это НЕ мониторинг удалённого репозитория. Для автоматического обнаружения
новых commit используйте:
  sudo bash install_auto_update.sh --yes
EOF2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Неизвестная опция: $1" >&2; exit 2 ;;
  esac
done

[[ -d "$REPO_ROOT/.git" ]] || { echo "$REPO_ROOT не является git checkout" >&2; exit 1; }
[[ -f "$REPO_ROOT/deploy_telegram_bot.sh" ]] || { echo "Не найден deploy_telegram_bot.sh" >&2; exit 1; }
mkdir -p "$HOOKS_DIR"

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "Установить post-merge/post-rewrite hooks для ветки $BRANCH? [Y/n]: " answer
  [[ -z "$answer" || "$answer" =~ ^[YyДд]$ ]] || exit 1
fi

write_hook() {
  local name="$1" path="$HOOKS_DIR/$1"
  cat >"$path" <<EOF2
#!/usr/bin/env bash
set -u
REPO_ROOT='$REPO_ROOT'
BRANCH='$BRANCH'
LOG_FILE='$LOG_FILE'
DEPLOY_SCRIPT='$REPO_ROOT/deploy_telegram_bot.sh'

current_branch="\$(git -C "\$REPO_ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[[ "\$current_branch" == "\$BRANCH" ]] || exit 0

{
  echo
  echo "==== \$(date -u +%Y-%m-%dT%H:%M:%SZ) $name ===="
  echo "rev=\$(git -C "\$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  if [[ "\$(id -u)" -eq 0 ]]; then
    bash "\$DEPLOY_SCRIPT" --yes
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo -n bash "\$DEPLOY_SCRIPT" --yes
  else
    echo "deploy deferred: нет non-interactive sudo; systemd auto-update timer развернёт отстающий checkout"
    exit 0
  fi
} >>"\$LOG_FILE" 2>&1
EOF2
  chmod +x "$path"
}

write_hook post-merge
write_hook post-rewrite
rm -f "$HOOKS_DIR/post-checkout"

echo "Hooks установлены: post-merge, post-rewrite"
echo "Remote monitoring: sudo bash install_auto_update.sh --yes"
echo "Лог: $LOG_FILE"
