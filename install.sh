#!/usr/bin/env bash
set -euo pipefail

APP_NAME="cc2-dash"
SERVICE_NAME="cc2-dash.service"
LEGACY_SERVICE_NAME="cc2-dash-lite.service"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
PORT="${CC2_PORT:-8088}"
INSTALL_SERVICE=0
INSTALL_SYSTEM_DEPS=1

for arg in "$@"; do
  case "$arg" in
    --service) INSTALL_SERVICE=1 ;;
    --no-system-deps) INSTALL_SYSTEM_DEPS=0 ;;
    --port=*) PORT="${arg#*=}" ;;
    -h|--help)
      cat <<HELP
$APP_NAME installer

Usage:
  ./install.sh [--service] [--port=8088] [--no-system-deps]

Options:
  --service          Install/update and start the systemd service.
  --port=PORT       Port for the service unit. Default: 8088.
  --no-system-deps  Skip apt install for python3-venv/python3-pip.
HELP
      exit 0
      ;;
  esac
done

say() { printf '\033[1;36m[cc2]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; exit 1; }
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
  for p in $(unit_file_paths "$SERVICE_NAME"); do [[ -e "$p" ]] && return 0; done
  [[ -e "/etc/systemd/system/multi-user.target.wants/$SERVICE_NAME" ]] && return 0
  return 1
}
stop_existing_service() {
  if service_exists; then
    say "Stopping existing $SERVICE_NAME before updating it"
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true
  fi
}

remove_legacy_service() {
  if ! have_systemctl; then
    return 0
  fi
  if systemctl list-unit-files --full --all "$LEGACY_SERVICE_NAME" 2>/dev/null | grep -q "^$LEGACY_SERVICE_NAME" \
    || systemctl list-units --full --all "$LEGACY_SERVICE_NAME" 2>/dev/null | grep -q "^$LEGACY_SERVICE_NAME" \
    || [[ -e "/etc/systemd/system/$LEGACY_SERVICE_NAME" ]]; then
    warn "Removing legacy $LEGACY_SERVICE_NAME so it cannot fight $SERVICE_NAME for the same port"
    sudo systemctl stop "$LEGACY_SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$LEGACY_SERVICE_NAME" 2>/dev/null || true
    sudo systemctl reset-failed "$LEGACY_SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$LEGACY_SERVICE_NAME" "/etc/systemd/system/multi-user.target.wants/$LEGACY_SERVICE_NAME"
    sudo rm -f "/lib/systemd/system/$LEGACY_SERVICE_NAME" "/usr/lib/systemd/system/$LEGACY_SERVICE_NAME" 2>/dev/null || true
    sudo systemctl daemon-reload || true
  fi
}

say "Installing $APP_NAME in $APP_DIR"
remove_legacy_service

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is required. Install Python 3 first."
fi

if [[ "$INSTALL_SYSTEM_DEPS" == "1" ]] && command -v apt-get >/dev/null 2>&1; then
  say "Checking system dependencies"
  if ! python3 -m venv --help >/dev/null 2>&1; then
    warn "python3-venv missing; installing with apt"
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip
  else
    ok "python3-venv available"
  fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
  say "Creating virtual environment"
  python3 -m venv "$VENV_DIR"
else
  ok "Virtual environment already exists"
fi

say "Upgrading pip/setuptools/wheel"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

say "Installing Python dependencies"
"$VENV_DIR/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/data"
chmod +x "$APP_DIR/run.sh" "$APP_DIR/uninstall.sh" || true

ok "Install complete"

if [[ "$INSTALL_SERVICE" == "1" ]]; then
  if ! have_systemctl; then
    fail "systemctl was not found. Run manually with ./run.sh instead."
  fi
  say "Installing/updating systemd service on port $PORT"
  CURRENT_USER="${SUDO_USER:-$USER}"
  stop_existing_service
  sudo tee "/etc/systemd/system/$SERVICE_NAME" >/dev/null <<SERVICE
[Unit]
Description=CC2 Dash
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
Environment=CC2_PORT=$PORT
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/python -m uvicorn cc2_dash.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
SERVICE
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  ok "Service installed and started: $SERVICE_NAME"
  say "Status: sudo systemctl status $SERVICE_NAME --no-pager"
  say "Open: http://<this-pi-ip>:$PORT/"
else
  say "Run manually with: ./run.sh"
  say "Install service later with: ./install.sh --service --port=$PORT"
fi
