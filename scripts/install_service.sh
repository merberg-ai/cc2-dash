#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
SERVICE=/etc/systemd/system/cc2-dash.service
sudo tee "$SERVICE" >/dev/null <<SERVICEEOF
[Unit]
Description=cc2-dash Elegoo Centauri Carbon 2 local dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=CC2_DASH_CONFIG=$APP_DIR/config/printers.json
Environment=CC2_DASH_APP_CONFIG=$APP_DIR/config/app.json
Environment=CC2_DASH_HOST=0.0.0.0
Environment=CC2_DASH_PORT=8088
ExecStart=$APP_DIR/scripts/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICEEOF
sudo systemctl daemon-reload
sudo systemctl enable cc2-dash.service
sudo systemctl restart cc2-dash.service
sudo systemctl status cc2-dash.service --no-pager
