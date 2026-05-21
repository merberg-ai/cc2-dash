# cc2-dash-v1.0

Mobile-first LAN dashboard for Elegoo Centauri Carbon 2 (CC2).

## Features
- Setup flow: scan (`/api/scan`) or manual add.
- Persistent local config JSON (`config/printers.json` by default).
- Live status polling and camera auto-load (direct, alt direct, proxy fallback).
- Printer controls: pause/resume/cancel, temperature, fan.
- Stock Elegoo fallback portal kept at `/portal-fullscreen`.
- Safety gating for printer methods.

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn cc2_dash.main:app --host 0.0.0.0 --port 8088
```

Open `http://<host>:8088/`.

## First run
1. Open dashboard.
2. Tap **Scan** (or enter manual IP/serial/PIN).
3. Save printer.
4. Dashboard loads status + camera.

## Camera behavior
Order:
1. `http://<printer-ip>:8080/`
2. `http://<printer-ip>:8080/?action=stream`
3. `/api/printers/{id}/camera/stream`

## Safety
- `allow_commands` controls semi-safe methods.
- `allow_dangerous_commands` gates destructive actions.

## Troubleshooting
- Scan fails: try manual IP + serial + PIN.
- MQTT connect fails: verify PIN/access code + serial + port 1883.
- Camera direct works but dashboard fails: test `/api/printers/{id}/camera/url` and proxy endpoint.
- Commands blocked: enable command toggles in saved config.

## Notes
- Keep `config/printers.json` private (contains access code).
- App keeps portal compatibility route intact.
