#!/usr/bin/env bash
set -euo pipefail

APP_NAME="cc2-dash"
SERVICE_NAME="cc2-dash.service"
LEGACY_SERVICE_NAME="cc2-dash-lite.service"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PURGE=0
REMOVE_APP=0
KILL_LEFTOVERS=1

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --remove-app) REMOVE_APP=1 ;;
    --no-kill-leftovers) KILL_LEFTOVERS=0 ;;
    -h|--help)
      cat <<HELP
$APP_NAME uninstaller

Usage:
  ./uninstall.sh [--purge] [--remove-app] [--no-kill-leftovers]

Options:
  --purge              Remove .venv and data/config too. Without this, your config stays.
  --remove-app         Remove the entire app folder after service cleanup. Use carefully.
  --no-kill-leftovers  Do not pkill stray uvicorn processes launched from this app folder.
HELP
      exit 0
      ;;
  esac
done

say() { printf '\033[1;36m[cc2]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
have_systemctl() { command -v systemctl >/dev/null 2>&1; }
unit_file_paths() {
  local svc="${1:-$SERVICE_NAME}"
  printf '%s\n' \
    "/etc/systemd/system/$svc" \
    "/lib/systemd/system/$svc" \
    "/usr/lib/systemd/system/$svc"
}
service_exists() {
  have_systemctl && systemctl list-unit-files --full --all "$SERVICE_NAME" 2>/dev/null | grep -q "^$SERVICE_NAME" && return 0
  have_systemctl && systemctl list-units --full --all "$SERVICE_NAME" 2>/dev/null | grep -q "^$SERVICE_NAME" && return 0
  for p in $(unit_file_paths "$SERVICE_NAME"); do [[ -e "$p" ]] && return 0; done
  [[ -e "/etc/systemd/system/multi-user.target.wants/$SERVICE_NAME" ]] && return 0
  return 1
}

remove_named_service() {
  local svc="$1"
  if have_systemctl; then
    sudo systemctl stop "$svc" 2>/dev/null || true
    sudo systemctl disable "$svc" 2>/dev/null || true
    sudo systemctl reset-failed "$svc" 2>/dev/null || true
  fi
  sudo rm -f "/etc/systemd/system/$svc" "/etc/systemd/system/multi-user.target.wants/$svc"
  sudo rm -f "/lib/systemd/system/$svc" "/usr/lib/systemd/system/$svc" 2>/dev/null || true
}

say "Removing $SERVICE_NAME if present"
if service_exists; then
  if have_systemctl; then
    remove_named_service "$SERVICE_NAME"
  fi
  have_systemctl && sudo systemctl daemon-reload || true
  have_systemctl && sudo systemctl reset-failed || true
  ok "Systemd service removed"
else
  warn "No systemd service file was found"
fi

say "Removing legacy $LEGACY_SERVICE_NAME if present"
remove_named_service "$LEGACY_SERVICE_NAME"
have_systemctl && sudo systemctl daemon-reload || true
have_systemctl && sudo systemctl reset-failed || true

if [[ "$KILL_LEFTOVERS" == "1" ]]; then
  say "Checking for stray cc2-dash uvicorn processes from this folder"
  pkill -f "$APP_DIR/.venv/bin/python -m uvicorn cc2_dash.main:app" 2>/dev/null || true
  pkill -f "uvicorn cc2_dash.main:app.*$APP_DIR" 2>/dev/null || true
  ok "Stray process cleanup attempted"
fi

if [[ "$PURGE" == "1" ]]; then
  say "Purging virtualenv and app data"
  rm -rf "$APP_DIR/.venv" "$APP_DIR/data"
  ok "Purged .venv and data"
else
  say "Keeping .venv and data. Use --purge to remove them."
fi

if [[ "$REMOVE_APP" == "1" ]]; then
  say "Removing app folder: $APP_DIR"
  cd /tmp
  rm -rf "$APP_DIR"
  ok "App folder removed"
else
  ok "Uninstall complete"
fi
