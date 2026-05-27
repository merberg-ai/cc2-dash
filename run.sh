#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
PORT="${CC2_PORT:-8088}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Virtualenv missing. Run ./install.sh first."
  exit 1
fi

cd "$APP_DIR"
exec "$VENV_DIR/bin/python" -m uvicorn cc2_dash_lite.main:app --host 0.0.0.0 --port "$PORT"
