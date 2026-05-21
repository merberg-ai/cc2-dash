# cc2-dash-v1.0

Local, mobile-first web dashboard/portal for the Elegoo Centauri Carbon 2.

## What this project does

CC2 local HTTP (`http://printer-ip/`) does not provide the full dashboard UI. This app wraps the three printer LAN services into one dashboard:

1. UDP discovery (`method:7000` on port `52700`)
2. MQTT printer API (`TCP/1883`, username `elegoo`, password = printer access code)
3. Camera MJPEG stream (`http://<printer-ip>:8080/`)

## Features implemented

- FastAPI backend with routes for:
  - discovery: `GET /api/discover`
  - printer CRUD: `GET/POST/PATCH/DELETE /api/printers`
  - restart/status: `POST /api/printers/{id}/restart`, `GET /api/printers/{id}/status`
  - generic command bridge: `POST /api/printers/{id}/command`
  - convenience controls: light/temp/fans/pause/resume/cancel/start
  - camera helpers: URL + proxy stream
  - portal wrappers: `/portal`, `/portal-fullscreen`
- MQTT client lifecycle manager and per-printer client.
- Safety gate by method id (`SAFE_METHODS`, `SEMI_SAFE_METHODS`, `DANGEROUS_METHODS`).
- Deep-merge status state + normalized status object for UI.
- Mobile-first frontend with setup/scan, status, camera, controls, console.

## Project layout

```text
cc2-dash/
  requirements.txt
  README.md
  scripts/
  config/
    printers.json
  cc2_dash/
    main.py
    config.py
    cc2/
      commands.py
      discovery.py
      client.py
      manager.py
      state.py
    web/
      index.html
      static/
        app.js
        style.css
        mobile.css
```

## Install / run
# cc2-dash

Local, mobile-first dashboard for the Elegoo Centauri Carbon 2.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn cc2_dash.main:app --host 0.0.0.0 --port 8088
```

Open `http://<server-ip>:8088/`

## Configuration

Default config file:

- `config/printers.json`

Override with:

- `CC2_DASH_CONFIG=/path/to/printers.json`

Example printer object:

```json
{
  "id": "centauri-carbon-2",
  "name": "Centauri Carbon 2",
  "host": "192.168.1.3",
  "serial": "F01UT8FKFZ1HFNR",
  "access_code": "123456",
  "port": 1883,
  "enabled": true,
  "allow_commands": false,
  "allow_dangerous_commands": false
}
```

## Safety defaults

Use safe defaults:

- `allow_commands: false`
- `allow_dangerous_commands: false`

This blocks command methods unless explicitly enabled.

## Camera notes

Frontend direct-first camera order:

1. `http://<printer-ip>:8080/`
2. `http://<printer-ip>:8080/?action=stream`
3. `/api/printers/{id}/camera/stream`

## Known limitations

- MQTT payloads/method ids are reverse-engineered and firmware-dependent.
- Discovery is subnet dependent and may be blocked by AP isolation/VLANs.
- Stock Elegoo portal assets in this repo are placeholders.
- Access code is stored plaintext in local JSON (LAN-only convenience).
Open: `http://<server-ip>:8088/`

## Notes

- Discovery uses UDP method 7000 on port 52700.
- Control and status use MQTT (`elegoo` / printer access code).
- Camera stream is direct MJPEG on `http://<printer-ip>:8080/`.
