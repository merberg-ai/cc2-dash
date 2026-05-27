#!/usr/bin/env bash
set -euo pipefail

APP_NAME="cc2-dash-lite"
SERVICE_NAME="cc2-dash-lite.service"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PURGE=0

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help)
      cat <<HELP
$APP_NAME uninstaller

Usage:
  ./uninstall.sh [--purge]

Options:
  --purge   Remove .venv and data/config too. Without this, your config stays.
HELP
      exit 0
      ;;
  esac
done

say() { printf '\033[1;36m[cc2]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }

say "Uninstalling service if present"
if systemctl list-unit-files | grep -q "^$SERVICE_NAME"; then
  sudo systemctl stop "$SERVICE_NAME" || true
  sudo systemctl disable "$SERVICE_NAME" || true
  sudo rm -f "/etc/systemd/system/$SERVICE_NAME"
  sudo systemctl daemon-reload
  ok "Service removed"
else
  warn "Service was not installed"
fi

if [[ "$PURGE" == "1" ]]; then
  say "Purging virtualenv and app data"
  rm -rf "$APP_DIR/.venv" "$APP_DIR/data"
  ok "Purged .venv and data"
else
  say "Keeping .venv and data. Use --purge to remove them."
fi

ok "Uninstall complete"
