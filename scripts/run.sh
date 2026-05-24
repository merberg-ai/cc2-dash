#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then
  echo ".venv not found. Running installer first..."
  ./scripts/install.sh
fi
. .venv/bin/activate
export CC2_DASH_CONFIG="${CC2_DASH_CONFIG:-config/printers.json}"
export CC2_DASH_APP_CONFIG="${CC2_DASH_APP_CONFIG:-config/app.json}"
HOST="${CC2_DASH_HOST:-0.0.0.0}"
PORT="${CC2_DASH_PORT:-8088}"
exec python -m uvicorn cc2_dash.main:app --host "$HOST" --port "$PORT"
