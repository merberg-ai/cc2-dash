# cc2-dash

Local LAN dashboard for the Elegoo Centauri Carbon 2 / CC2.

This build keeps the working CC2 LAN pieces and starts turning them into a full custom dashboard:

- UDP discovery / scan on the local network
- printer pairing with the access code/PIN
- MQTT register/status/request flow
- status normalization for dashboard cards
- direct camera stream support at `http://<printer-ip>:8080/`
- backend camera proxy fallback
- stock Elegoo portal bridge
- G-code file list/detail/start/delete endpoints
- print history / timelapse-oriented endpoints
- persisted dashboard settings in `config/app.json`
- mobile/touch browser detection in the UI
- safer LAN defaults: no wildcard CORS by default, redacted access codes, destructive controls disabled until enabled

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/run.sh
```

Open:

```text
http://<server-ip>:8088/
```

## Config files

```text
config/printers.json   # paired printer configs / access codes
config/app.json        # dashboard, mobile, camera, safety, theme, layout settings
```

The dashboard does **not** echo printer access codes through `/api/printers`. In the Settings tab, the access code field is blank on purpose; enter a new one only when you want to replace it.

## LAN-only safety notes

This app is designed for your local LAN only. It still sends real printer commands, so the defaults are intentionally conservative:

- wildcard CORS is disabled by default
- printer controls default off for newly added printers
- destructive controls default off
- cancel/start/delete confirmations default on
- temperature/fan commands are clamped by settings in `config/app.json`

## Useful endpoints

```text
GET    /api/health
GET    /api/settings
PATCH  /api/settings
POST   /api/settings/reset
GET    /api/settings/export
GET    /api/discover
GET    /api/printers
POST   /api/printers
PATCH  /api/printers/{printer_id}
DELETE /api/printers/{printer_id}
GET    /api/printers/{printer_id}/status
POST   /api/printers/{printer_id}/command
GET    /api/printers/{printer_id}/files
POST   /api/printers/{printer_id}/files/start
POST   /api/printers/{printer_id}/files/delete
GET    /api/printers/{printer_id}/history
POST   /api/printers/{printer_id}/history/delete
GET    /api/printers/{printer_id}/camera/url
GET    /api/printers/{printer_id}/camera/stream
GET    /portal-fullscreen
```

## Service install

```bash
./scripts/install.sh
sudo ./scripts/install_service.sh
```

Remove service:

```bash
sudo ./scripts/uninstall_service.sh
```


## Local authentication

cc2-dash now includes local LAN authentication. On first launch, the UI asks you to create the first admin user. There is no default password.

- Password hashes are stored in `config/users.json`.
- App/auth settings are stored in `config/app.json`.
- Guests can view a configurable read-only dashboard.
- Full controls, pairing, settings, file deletion, stock portal access, and debug routes require login and role permissions.
- Roles are `viewer`, `operator`, and `admin`; unauthenticated users are `guest`.

If you delete `config/users.json`, cc2-dash returns to first-run admin setup mode.
