#!/usr/bin/env bash
set -euo pipefail
sudo systemctl stop cc2-dash.service || true
sudo systemctl disable cc2-dash.service || true
sudo rm -f /etc/systemd/system/cc2-dash.service
sudo systemctl daemon-reload
echo "cc2-dash service removed. Project files were not deleted."
