#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -r requirements.txt
echo
echo "Installed. Run with: ./scripts/run.sh"
