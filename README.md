
### v1.2.12 portal navigation fix

- The top navigation **Portal** link now matches the dashboard **Go To Elegoo Web Portal** button behavior.
- It opens the fullscreen Elegoo portal view in a new browser tab instead of loading the portal chrome page inside the current page.
- This avoids the awkward nested-wrapper/iframe-in-iframe layout when using the top navigation.

# cc2-dash-lite

**cc2-dash-lite** is a lightweight, mobile-first dashboard and local portal shell for the Elegoo Centauri Carbon 2 / CC2 ecosystem. It provides a clean LAN dashboard, printer discovery and pairing, access controls, configurable navigation, a bundled stock Elegoo portal view, file/timelapse helpers, filament/CANVAS status experiments, and optional Portal AI monitoring with Ollama vision support.

> [!WARNING]
> **Personal / home / hobbyist use only.** This project is not designed, tested, or recommended for production, commercial print farms, safety-critical environments, remote unattended operation, or any situation where a failed command, missed detection, or incorrect AI result could cause damage. Use it on a trusted LAN, keep physical access to the printer, and treat all AI/vision output as advisory.

> [!IMPORTANT]
> This is an unofficial community-style dashboard. It is not a replacement for the stock Elegoo portal, and it is not affiliated with or endorsed by Elegoo or OctoEverywhere. The **Go To Elegoo Web Portal** button remains available as the primary fallback.

---

## Table of contents

- [Project goals](#project-goals)
- [Feature overview](#feature-overview)
- [Requirements](#requirements)
- [Quick install](#quick-install)
- [Install as a systemd service](#install-as-a-systemd-service)
- [First-run setup wizard](#first-run-setup-wizard)
- [Using the dashboard](#using-the-dashboard)
- [Printer Manager](#printer-manager)
- [Camera Relay / stream protection](#camera-relay--stream-protection)
- [Portal AI and Ollama vision monitoring](#portal-ai-and-ollama-vision-monitoring)
- [AI feedback / dataset collection](#ai-feedback--dataset-collection)
- [Logs](#logs)
- [File Manager](#file-manager)
- [Filament Manager](#filament-manager)
- [Stock Elegoo portal bridge](#stock-elegoo-portal-bridge)
- [Commands and safety gates](#commands-and-safety-gates)
- [Configuration](#configuration)
- [Theme and UI customization](#theme-and-ui-customization)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Uninstall](#uninstall)
- [Release notes](#release-notes)

---

## Project goals

cc2-dash-lite is intended to be:

- **Local-first**: designed for use on a trusted home LAN.
- **Mobile-friendly**: usable from a phone or tablet while walking around the printer room.
- **Beginner approachable**: first-run setup guides you through discovery, manual add, UI settings, access controls, and AI monitoring.
- **Transparent**: key events are logged, risky controls are gated, and Portal AI explains the reasons behind its risk level.
- **Extendable**: file management, filament management, CANVAS/MMS support, and AI monitoring can be improved over time without replacing the whole dashboard.

It is not trying to be a hardened production control platform. Keep the stock portal and the printer's physical controls available.

---

## Feature overview

### Core application

- FastAPI backend.
- Mobile-first dashboard UI using plain CSS and vanilla JavaScript.
- First-run card-by-card setup wizard.
- Raspberry Pi / Linux install, uninstall, virtualenv, and optional systemd service scripts.
- LAN allowlist guard, defaulting to `192.168.1.0/24` plus localhost.
- Header build badge showing version plus Git/GitHub commit metadata when available.
- `/api/version` and `/health` diagnostics.

### Printer discovery and pairing

- UDP Centauri discovery using Elegoo method `7000`.
- Verified-printer scan filtering so unrelated LAN devices are hidden.
- Manual printer add when discovery is blocked.
- PIN/access-code pairing during setup.
- MQTT client using printer serial plus PIN/access code.
- Normalized printer status from the CC2 MQTT status stream.
- Settings → Printer Manager for scan/manual add/edit/remove/default printer control.

### Dashboard and controls

- Configurable dashboard card visibility and ordering.
- Configurable quick-action button visibility, ordering, confirmation, and labels.
- Light toggle, pause/resume/cancel, camera wake, speed preset selector, and Analyze Camera Now quick actions where enabled.
- Dashboard telemetry display for current printer speed preset when reported by the printer.
- Camera Relay fanout endpoint: cc2-dash-lite keeps one upstream printer camera connection and serves dashboard clients, snapshots, watchdog vision, and portal rewrites from the local relay.

### Stock portal integration

- Bundled stock Elegoo/OctoEverywhere-style portal bundle from the older cc2-dash source.
- Local MQTT-over-WebSocket bridge for the stock portal.
- Fullscreen stock portal route and wrapper portal route.
- Camera URL rewrite shim that attempts to route embedded stock-portal camera views through the local Camera Relay instead of directly opening printer `:8080`.

### File, timelapse, and filament tools

- File Manager page for G-code files and timelapse/history video records.
- G-code file list/detail/start/delete endpoints from the stock portal command set.
- Timelapse/history load/export/download/delete controls where firmware allows it.
- Filament Manager page for stock-style CANVAS/MMS filament tray information.
- Configurable File Manager and Filament Manager menu visibility.

### Camera Relay / stream protection

The CC2 camera endpoint can become unhappy when multiple browser tabs, the stock portal, the slicer, and background vision checks all connect directly to the printer camera stream. cc2-dash-lite now includes a local **Camera Relay** to reduce that connection pileup.

How it works:

1. The backend opens one upstream MJPEG connection to the printer camera.
2. The latest JPEG frame is kept in memory.
3. Dashboard viewers receive a local MJPEG fanout stream from cc2-dash-lite.
4. Portal AI / Ollama vision grabs the cached latest frame instead of opening its own direct camera connection.
5. `/api/printers/<id>/camera/snapshot.jpg` returns the latest cached frame.
6. The embedded stock portal has a camera rewrite shim that tries to redirect direct `http://<printer>:8080/` camera references through the relay.

Useful endpoints:

```text
GET  /api/printers/<id>/camera/stream
GET  /api/printers/<id>/camera/snapshot.jpg
GET  /api/printers/<id>/camera/latest.jpg
GET  /api/printers/<id>/camera/status
GET  /api/camera/status
POST /api/printers/<id>/camera/restart
```

Settings are available under **Settings → Camera Relay / Stream Protection**. Recommended defaults are relay enabled, start on boot enabled, portal rewrites enabled, and direct fallback disabled. Direct fallback can help debugging, but it can also recreate the original too-many-connections problem.

> [!NOTE]
> This protects traffic that goes through cc2-dash-lite. If Elegoo Slicer or another external app connects directly to the printer camera, that still consumes its own printer-side connection. The relay reduces cc2-dash-lite's footprint from many camera connections down to one.

## Portal AI monitoring

- Portal AI telemetry failure detection with explainable risk score.
- Background watchdog monitoring, even when the browser is closed.
- Optional Ollama vision monitoring using printer camera snapshots.
- Configurable Ollama host, model loading, model testing, and model pull request support.
- Local camera-frame heuristics for dark/low-contrast frames and stringing-style fine-edge warnings.
- Portal AI feedback buttons for Looks Good / Looks Bad / False Alarm dataset collection.
- Filterable persisted Logs page for system, command, Portal AI, scanner, filament, and vision events.

---

## Requirements

Recommended target:

- Raspberry Pi 4, Raspberry Pi 5, or another small Linux machine.
- Python 3.10+.
- Network access to the Centauri Carbon 2 / CC2 printer.
- Printer and dashboard host on the same trusted LAN.
- Optional: local Ollama host for vision monitoring.

Python dependencies are installed by `install.sh` from `requirements.txt`:

```text
fastapi
httpx
jinja2
paho-mqtt
pydantic
python-multipart
requests
uvicorn[standard]
Pillow
```

> [!NOTE]
> The installer can install `python3-venv` and `python3-pip` with `apt` on Debian/Raspberry Pi OS systems. Use `--no-system-deps` if you want to manage system packages yourself.

---

## Quick install

### 1. Extract the project

```bash
unzip cc2-dash-lite-1.2.11.zip
cd cc2-dash-lite
```

If your extracted folder has a versioned name, either `cd` into that folder or rename it:

```bash
mv cc2-dash-lite-1.2.11 cc2-dash-lite
cd cc2-dash-lite
```

### 2. Run the installer

```bash
chmod +x install.sh run.sh uninstall.sh
./install.sh
```

The installer will:

1. Check for Python 3.
2. Create `.venv/` if needed.
3. Upgrade `pip`, `setuptools`, and `wheel`.
4. Install Python dependencies.
5. Create `data/` for runtime config/logs.

### 3. Start the app manually

```bash
./run.sh
```

Then open the dashboard in a browser:

```text
http://<pi-ip>:8088/
```

Example:

```text
http://192.168.1.50:8088/
```

---

## Install as a systemd service

To install and start cc2-dash-lite as a background service:

```bash
./install.sh --service --port=8088
```

Useful service commands:

```bash
sudo systemctl status cc2-dash-lite
sudo systemctl restart cc2-dash-lite
sudo systemctl stop cc2-dash-lite
sudo journalctl -u cc2-dash-lite -f
```

The generated service runs:

```bash
python -m uvicorn cc2_dash_lite.main:app --host 0.0.0.0 --port 8088
```

You can also override the port manually:

```bash
CC2_PORT=8090 ./run.sh
```

---

## First-run setup wizard

When no valid printer is configured, cc2-dash-lite opens `/setup` and walks through a centered card-by-card wizard.

Setup flow:

| Step | Purpose |
|---:|---|
| 1 | Welcome and scan for verified Centauri printers |
| 2 | Optional manual printer add |
| 3 | UI setup: theme and font choices |
| 4 | Network access allowlist |
| 5 | Portal AI, failure detection, and Ollama vision settings |
| 6 | Summary and launch dashboard |

The scanner only shows devices that answer the Centauri discovery probe. Routers, Tasmota plugs, phones, and unrelated LAN web interfaces are hidden instead of being offered as printer candidates.

The wizard saves the information the CC2 needs:

```text
Printer IP / host
Printer serial / SN
Printer PIN / access code
MQTT port, default 1883
Default printer selection
```

Discovery usually fills the serial automatically. If discovery is blocked by your network, use manual add with the printer IP, serial number, and PIN/access code.

If a previous build saved an incomplete printer entry without serial/PIN, this build routes you back through setup instead of assuming the printer is paired.

---

## Using the dashboard

Primary navigation:

| Page | Description |
|---|---|
| **Dash** | Main status view with printer telemetry, camera, quick actions, Portal AI, and cards. |
| **Portal** | Wrapper view for the bundled stock Elegoo portal. |
| **Files** | Optional File Manager for G-code and timelapse/history records. |
| **Filament** | Optional Filament Manager for CANVAS/MMS tray data. |
| **Settings** | Theme, features, quick actions, Printer Manager, access, and Portal AI settings. |
| **Logs** | Filterable system, command, scanner, Portal AI, filament, and vision logs. |

The **Files** and **Filament** menu items can be shown or hidden in **Settings → Menu / Features**.

---

## Printer Manager

Open **Settings → Printer Manager** to manage configured printers after setup.

Available actions:

- Scan for verified printers.
- Add a printer manually.
- Edit display name, host/IP, serial, PIN/access code, and MQTT port.
- Enable or disable a printer entry.
- Choose the default printer.
- Enable or disable command permissions.
- Enable or disable dangerous command permissions.
- Remove old printer entries.

Command permissions are intentionally separate from discovery/pairing. A printer can be visible and monitored while potentially destructive actions remain blocked.

---

## Portal AI and Ollama vision monitoring

Portal AI is an advisory monitoring layer. It combines printer telemetry, local rules, optional camera-frame heuristics, and optional Ollama vision analysis.

> [!CAUTION]
> Portal AI does **not** make the printer safe to leave unattended. It can miss failures, produce false alarms, or misunderstand camera images. Use it as an extra status signal, not as a safety system.

Current checks include:

- Printer reachable / connected / registered.
- Stale MQTT status age.
- Printer error/fail/emergency/stopped states.
- Paused state warning.
- Printer exception status.
- Progress stuck timer.
- Hotend/bed target sanity while a print appears active.
- Filament sensor reports no filament while printing.
- Printer-reported camera availability hints.
- Optional Ollama camera-frame analysis.
- Local frame checks for dark camera images and high fine-edge/stringing-style changes.

The background watchdog starts with the FastAPI service and keeps evaluating configured printers on a timer, even if nobody has the dashboard open. The dashboard displays the latest cached watchdog result when available.

### Ollama setup

Enable Ollama vision in **Settings → Portal AI → Ollama vision monitoring**.

Defaults:

```text
Ollama host:port: http://192.168.1.24:11434
Vision model: llava
Vision interval: 120 seconds
Bad checks required: 2
```

Useful controls:

| Control | Purpose |
|---|---|
| **Load Models** | Fetch installed models from the configured Ollama `/api/tags` endpoint. |
| **Model dropdown** | Select the vision model using the active theme styling. |
| **Test** | Confirm that the selected model is available. |
| **Pull** | Request an Ollama model pull by name. |
| **Analyze Camera Now** | Force an immediate one-shot camera analysis from the dashboard. |

Vision monitoring stores the latest frame under:

```text
data/vision/<printer_id>/latest.jpg
```

The vision result is merged into the Portal AI risk score. This build remains advisory-only and does not pause or cancel prints automatically.

### Related API endpoints

```text
GET  /api/ai/monitor
GET  /api/printers/<printer_id>/ai/status
POST /api/printers/<printer_id>/ai/check-now
POST /api/printers/<printer_id>/ai/feedback
GET  /api/printers/<printer_id>/vision/status
GET  /api/vision/models
POST /api/vision/pull
POST /api/printers/<printer_id>/vision/check-now
GET  /api/printers/<printer_id>/vision/latest.jpg
```

---

## AI feedback / dataset collection

When Portal AI feedback is enabled, the dashboard shows:

- **Looks Good**
- **Looks Bad**
- **False Alarm**

Feedback is saved to:

```text
data/ai_feedback.jsonl
data/ai_feedback_frames/<printer_id>/
```

Each feedback record can include:

- Feedback label and optional note.
- Current printer status snapshot.
- Current Portal AI result.
- Latest vision result.
- Client/UI context.
- A stable copy of the latest vision frame when available.

Review endpoints:

```text
GET /api/ai/feedback/recent
GET /api/ai/feedback/stats
```

Feedback currently builds a labeled dataset for later review and tuning. It does **not** automatically train, fine-tune, or adjust live scoring. That is intentional: collect examples first, review them, then use them for calibration later.

---

## Logs

The Logs page reads from the in-memory console and persisted JSONL logs:

```text
data/logs/system.jsonl
```

Filters include:

- Source.
- Level.
- Search text.
- Row limit.

Common sources:

```text
system
app
setup
settings
scanner
command
portal_ai
vision
filament
```

Portal AI watchdog changes and vision state changes are logged automatically. Vision logs can include flags such as:

```text
dark_frame
low_contrast_frame
light_drop_detected
high_fine_edge_density
fine_edge_density_jump
telemetry_model_mismatch
```

---

## File Manager

The top navigation includes **Files** when enabled in **Settings → Menu / Features**.

The page has two panels:

| Panel | Purpose |
|---|---|
| **G-code Files** | Local/USB file list, info, print, and delete. |
| **Timelapse Videos** | Timelapse/history records, download/export/delete where firmware allows it. |

The File Manager uses the same CC2/Elegoo MQTT command family that the stock portal code uses. Some firmware builds return slightly different JSON shapes, so the frontend checks several known list keys before reporting that no usable list was returned.

> [!CAUTION]
> `Start Print`, `Delete File`, and `Delete History/Timelapse` are blocked by the backend unless that printer has dangerous commands enabled.

---

## Filament Manager

The Filament Manager is available from the top navigation when enabled in **Settings → Menu / Features**:

```text
/filaments
```

It mimics the stock Elegoo filament information panel using cc2-dash-lite theme cards. It reads CC2/CANVAS filament data from the local MQTT command/status path, primarily method `2005` (`GET_CANVAS_STATUS`), then normalizes the stock-style object shape.

Expected data may include:

```text
mmsSystemName
mmsList[]
trayList[]
trayName / trayId
filamentType / filamentName / filamentColor
vendor / serialNumber / weight / diameter
temperature ranges when reported
tray status
```

The page shows:

- Summary tiles.
- Tray cards.
- Filament sensor state.
- Auto Filament Refill control.

Auto refill uses method `2004` with compatible enable/disable parameter aliases. This is treated as a normal command, not a dangerous command, but the printer still needs commands enabled in **Settings → Printer Manager**.

If the Combo/CANVAS system does not report tray data yet, the page falls back to telemetry-only information and states that no filament data was available. Use **Refresh** after the printer has had time to publish telemetry.

> [!NOTE]
> Filament Manager support is still experimental and may need adjustment for firmware-specific CANVAS/MMS response shapes.

---

## Stock Elegoo portal bridge

The stock portal routes are:

```text
/portal-fullscreen
/portal
/elegoo/octo_portal.html
```

The local bridge is:

```text
/ws/mqtt/<printer_id>
```

That bridge shuttles browser WebSocket MQTT frames to the printer's TCP MQTT port, usually `1883`.

The bundled stock portal is kept as the fallback and reference view. If a cc2-dash-lite feature does not yet expose something cleanly, use the stock portal button.

---

## Commands and safety gates

Current command mapping:

| Feature | Method / behavior |
|---|---|
| File Manager | `1044`, `1046`, `1047`, `1051`, `1020`, `1038` |
| Filament Manager | `2005` CANVAS status, `2004` Auto Filament Refill |
| Light Toggle | `1029` |
| Pause Print | `1021` |
| Resume Print | `1023` |
| Cancel Print | `1022` |
| Camera Wake/Enable | `1042` / `1054` |
| Set Speed Preset | `1031` with params `{ "mode": 0-3 }` |
| Analyze Camera Now | Server-side Ollama vision check |

Speed preset modes:

| Mode | Label |
|---:|---|
| `0` | Silent |
| `1` | Balanced |
| `2` | Sport |
| `3` | Ludicrous / Frenzy |

By default, non-dangerous commands are enabled for newly paired printers. Dangerous commands are disabled by default. This helps prevent accidental `Cancel Print`, `Delete File`, or `Start Print` actions from a mobile tap.

---

## Configuration

Default config path:

```text
./data/config.json
```

Useful environment variables:

```bash
export CC2_DATA_DIR=/path/to/data
export CC2_CONFIG=/path/to/config.json
export CC2_PORT=8088
```

Runtime data commonly appears under:

```text
data/config.json
data/logs/system.jsonl
data/vision/<printer_id>/latest.jpg
data/ai_feedback.jsonl
data/ai_feedback_frames/<printer_id>/
```

### Access allowlist

The application includes a LAN allowlist guard. The default is:

```text
192.168.1.0/24
localhost
```

Configure this during first-run setup or later in Settings. Keep this restricted to trusted local IP ranges.

---

## Theme and UI customization

Themes live in:

```text
cc2_dash_lite/themes.py
```

Included themes:

- Octo Dark Blue
- Amber Terminal
- Mainsail-ish Dark
- Carbon Glass
- High Contrast

Fonts are CSS font stacks only. No external font files are bundled.

The runtime UI uses plain CSS and vanilla JavaScript so it can run on a Raspberry Pi without a frontend build step. A starter Tailwind config is included under `frontend/` for future builds, but Tailwind is not required to run the app.

---

## Project layout

```text
cc2-dash-lite/
├── cc2_dash_lite/
│   ├── main.py
│   ├── config.py
│   ├── printer_client.py
│   ├── scanner.py
│   ├── themes.py
│   ├── logger.py
│   ├── ai.py
│   ├── vision.py
│   ├── build_info.py
│   ├── cc2/
│   │   ├── client.py
│   │   ├── commands.py
│   │   ├── discovery.py
│   │   ├── runtime.py
│   │   └── state.py
│   └── elegoo_web/
│       ├── octo_portal.html
│       └── cc2dash-shim.js
├── static/
│   ├── app.css
│   └── app.js
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── setup.html
│   ├── settings.html
│   ├── logs.html
│   ├── files.html
│   ├── filaments.html
│   └── portal.html
├── frontend/
├── install.sh
├── uninstall.sh
├── run.sh
├── requirements.txt
└── README.md
```

---

## Troubleshooting

### The dashboard will not start

Run:

```bash
./install.sh
./run.sh
```

If using systemd:

```bash
sudo systemctl status cc2-dash-lite
sudo journalctl -u cc2-dash-lite -f
```

### Browser cannot reach the dashboard

Check:

1. The Pi/Linux host IP address.
2. The port, default `8088`.
3. Your firewall/router rules.
4. The app access allowlist.

### Scan does not find the printer

Try:

1. Confirm the printer is powered on and connected to the same LAN.
2. Confirm the dashboard host is on the same subnet.
3. Try the direct printer IP in the setup scan box.
4. Use manual add with printer IP, serial number, and PIN/access code.
5. Check **Logs → scanner**.

The scan UI only shows verified Centauri responses. A generic open web port is not enough to appear as a printer.

### Stock portal opens but does not control the printer

Check:

1. Printer serial and PIN/access code.
2. MQTT port, usually `1883`.
3. Printer Manager command permission toggles.
4. Browser console and **Logs → command**.

### Ollama model list does not load

Check:

1. Ollama is running on the LAN host.
2. The configured Ollama URL includes protocol and port, for example `http://192.168.1.24:11434`.
3. The dashboard host can reach that IP/port.
4. The selected model is installed or can be pulled.

### Vision says the wrong printer state

Vision models analyze still images and can misinterpret whether a printer is actively printing. cc2-dash-lite sends telemetry context with vision prompts and logs `telemetry_model_mismatch` when camera interpretation conflicts with printer status.

### File Manager or Filament Manager returns blank data

These features depend on firmware-specific stock command responses. Use the stock portal as the fallback, then check:

```text
Logs → command
Logs → filament
Browser console
```

---

## Known limitations

- This project is not production-hardened.
- Portal AI is advisory only and can produce false positives or false negatives.
- Vision monitoring depends on camera image quality, lighting, model behavior, and Ollama performance.
- File Manager and Filament Manager support may need firmware-specific refinement.
- Some stock portal command responses vary by firmware version.
- Dangerous actions are intentionally blocked unless explicitly enabled.
- The frontend does not currently require a Node build pipeline; the `frontend/` folder is reserved for future work.

---

## Uninstall

Remove the service while keeping config and data:

```bash
./uninstall.sh
```

Remove service plus `.venv` and `data/`:

```bash
./uninstall.sh --purge
```

> [!CAUTION]
> `--purge` deletes local configuration, logs, vision frames, and feedback data stored under `data/`.

---

## Release notes

### v1.2.10

- Removed informal joke-style references from source comments, UI text, and documentation.
- Reworked `README.md` into a professional GitHub-style guide with beginner-friendly setup, feature walkthrough, safety warnings, troubleshooting, and full command/feature notes.
- Bumped package version metadata to `1.2.10`.

### v1.2.9

- Added the Filament Manager page and hideable Filament navigation item.
- Added CANVAS/MMS filament status normalization using method `2005`.
- Added Auto Filament Refill controls using method `2004`.
- Added README notes for Filament Manager and command mapping.

### v1.2.8

- Reworked first-run setup into a centered card-by-card wizard.
- Added scan, optional manual add, UI setup, access setup, Portal AI setup, and finish cards.
- Added Ollama model load/test/pull controls inside first-run AI setup.
- Updated documentation for the first-run flow and Printer Manager.

### v1.2.7

- Tightened printer discovery so the scan UI only shows verified Centauri Carbon candidates from UDP method-7000 discovery.
- Generic TCP/HTTP scan hits are treated as hidden hints instead of pairable devices.
- Added Settings → Printer Manager with verified scan, manual add, edit/save, make default, remove, enabled/commands/dangerous toggles, and per-printer connection settings.

### v1.2.6

- Dashboard quick-action buttons for **Analyze Camera Now** and **Set Speed** now use the active theme color instead of the plain secondary/card style.
- Portal AI feedback buttons now use theme-matched colors while still visually separating good/bad/false-alarm labels.
- AI feedback saves a richer labeled review record to `data/ai_feedback.jsonl` and can copy latest vision frames to `data/ai_feedback_frames/<printer_id>/`.
- Added feedback review endpoints:
  - `GET /api/ai/feedback/recent`
  - `GET /api/ai/feedback/stats`

### v1.2.5

- Vision prompts include printer telemetry context before asking Ollama to classify the camera image.
- Added a telemetry/model mismatch guard.
- Local frame heuristics are more sensitive to lights-off tests.
- Vision card shows luma/contrast/edge metrics.
- Speed telemetry is shown in the Status block and telemetry grid.

### v1.2.3

- Settings → Portal AI has a themed Ollama model dropdown.
- **Load Models** fetches installed models from the configured Ollama host.
- Added **Pull** model support via Ollama `/api/pull`.
- Dashboard hides the vision status block when Ollama vision monitoring is disabled.
- Set Speed quick action shows a dashboard selector for Silent / Balanced / Sport / Ludicrous and sends the selected mode at click time.

### v1.2.1

- Header shows a small build badge near the app name, for example `v1.2.1 · commit abc1234`.
- Added runtime build metadata detection from Git checkout or environment variables such as `CC2_DASH_GIT_COMMIT`, `GITHUB_SHA`, `CC2_DASH_GIT_BRANCH`, and `GITHUB_REF_NAME`.
- Added `/api/version` and included build metadata in `/health`.

### v1.2.0

- Portal AI background watchdog task starts with the service.
- Monitoring continues when the dashboard/browser is closed.
- `/api/status` serves cached watchdog results when available.
- Added Settings → Portal AI controls for background monitor enable/disable, check interval, log-on-change behavior, and minimum watchdog log level.
- Added `/api/ai/monitor`.

### v1.1.1

- Portal AI adds configurable multi-color / filament-swap progress-stall grace.
- Feedback labels are persisted to `data/ai_feedback.jsonl` for later tuning.

### v1.0.0 stable checkpoint

This release marked a known-good baseline before heavier feature work.

Included:

- First-run setup wizard with scan, PIN/access-code pairing, and saved printer configuration.
- Bundled stock Elegoo portal and local MQTT-over-WebSocket bridge.
- Mobile-first dashboard shell.
- Theme/font system.
- Configurable dashboard cards and quick-action buttons.
- Configurable top menu features, including File Manager visibility.
- Files route and backend endpoints retained for later refinement.
- Raspberry Pi/Linux install, uninstall, virtualenv, and optional systemd service scripts.

### v0.3.x file/timelapse notes

- Files/timelapse read endpoints no longer convert printer `error_code` responses into HTTP 500s.
- Timelapse listing loads through print history like the stock Elegoo portal; method `1051` is kept for export only.
- USB file-list errors show a friendly message instead of crashing the Files page.
- Timelapse tab mirrors the stock Elegoo portal Video List behavior more closely.
- Backend filters Print History rows for timelapse records using `TimeLapseVideoStatus` 1/2 or video URL/size/duration markers.
- Added fallback task-detail lookup using method `1037` when history rows do not include video metadata directly.
- UI shows size, creation time, duration, generated/export-needed status, and download/export actions.
- File Manager menu option toggle was added under **Settings → Menu / Features**.

---

## Development notes

No frontend build step is required for normal use. For quick validation after edits:

```bash
python -m compileall cc2_dash_lite
python - <<'PY'
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
env = Environment(loader=FileSystemLoader('templates'))
for path in Path('templates').glob('*.html'):
    env.get_template(path.name)
print('templates ok')
PY
node --check static/app.js
```

The `node --check` step is optional and only checks JavaScript syntax if Node is installed.
