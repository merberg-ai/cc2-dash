#!/usr/bin/env bash
set -euo pipefail
source .venv/bin/activate
exec uvicorn cc2_dash.main:app --host 0.0.0.0 --port 8088
