# cc2-dash

![Version](https://img.shields.io/badge/version-1.2.34-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20%2F%20Linux-green)
![Use](https://img.shields.io/badge/use-private%20hobbyist%20LAN-orange)

**cc2-dash** is a lightweight local dashboard and portal shell for the **Elegoo Centauri Carbon 2 / CC2** ecosystem. It gives you a clean LAN dashboard, printer discovery and pairing, camera relay/fanout, a stock Elegoo portal bridge, optional Ollama-powered visual monitoring, feedback-aware AI review tools, kiosk mode, file/history helpers, CANVAS filament controls, and a themeable mobile-friendly UI.

It is designed for a Raspberry Pi-style board sitting on your trusted home network. Think: printer-room companion dashboard, not enterprise print-farm overlord.

> [!WARNING]
> **Private, home, hobbyist use only.** cc2-dash is not designed, tested, or recommended for production environments, commercial print farms, safety-critical workflows, unattended remote operation, or any situation where missed detection, a failed command, or an incorrect AI result could cause damage. Keep physical access to your printer and use the stock printer controls as the final authority.

> [!IMPORTANT]
> This is an unofficial project. It is not affiliated with, endorsed by, or supported by Elegoo, OctoEverywhere, or any printer vendor. Firmware behavior can change. Some stock command paths behave differently across firmware versions.

> [!NOTE]
> In this version, **Portal AI cannot pause, resume, cancel, or otherwise control print jobs automatically**. AI/vision monitoring is advisory only. Manual dashboard controls can still be enabled by the user, but the AI watchdog does not issue pause/cancel commands yet.

---

## Table of contents

- [Current status](#current-status)
- [Tested hardware and platform notes](#tested-hardware-and-platform-notes)
- [Feature overview](#feature-overview)
- [What cc2-dash does not do](#what-cc2-dash-does-not-do)
- [Install from GitHub on Raspberry Pi OS](#install-from-github-on-raspberry-pi-os)
- [Run manually](#run-manually)
- [Install as a systemd service](#install-as-a-systemd-service)
- [Update from GitHub](#update-from-github)
- [First-run setup](#first-run-setup)
- [Using the dashboard](#using-the-dashboard)
- [Printer Manager](#printer-manager)
- [Camera Relay / stream protection](#camera-relay--stream-protection)
- [Kiosk mode](#kiosk-mode)
- [Portal AI and Ollama vision](#portal-ai-and-ollama-vision)
- [AI feedback and false-alarm suppression](#ai-feedback-and-false-alarm-suppression)
- [Persistent AI learning](#persistent-ai-learning)
- [File Manager](#file-manager)
- [Filament Manager / CANVAS controls](#filament-manager--canvas-controls)
- [Stock Elegoo portal bridge](#stock-elegoo-portal-bridge)
- [Themes and appearance](#themes-and-appearance)
- [Logs and diagnostics](#logs-and-diagnostics)
- [Safety gates and command behavior](#safety-gates-and-command-behavior)
- [Configuration and data paths](#configuration-and-data-paths)
- [Useful API endpoints](#useful-api-endpoints)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Uninstall](#uninstall)
- [Project layout](#project-layout)
- [Release notes](#release-notes)
- [Development checks](#development-checks)

---

## Current status

Current documented version:

```text
1.2.34 persistent-ai-learning-foundation
```

Major current capabilities:

| Area | Status |
|---|---|
| Printer discovery / pairing | Working, verified Centauri discovery filtering |
| Dashboard status | Working, mobile-first, active/idle aware |
| Stock portal bridge | Working as fallback/reference portal |
| Camera relay | Working, reduces direct camera connection pileups |
| Kiosk mode | Working, camera-first fullscreen view |
| Portal AI telemetry checks | Working, advisory-only |
| Ollama vision checks | Working, active-print-only by default |
| AI feedback dataset | Working, includes fresh-frame capture, JSONL audit log, SQLite mirror, and outcome interpretation |
| False-alarm suppression | Working for similar low/severity warnings on the same active print |
| Persistent AI learning | Foundation added: SQLite samples, per-printer profiles, rebuild/reset/status APIs, suggest-only by default |
| File Manager | Available but hidden by default because firmware timelapse/export behavior can be flaky |
| Filament Manager / CANVAS | Available but hidden by default while command behavior is tested on real firmware |
| Themes | Built-in theme library with preview cards |
| Windows support | Not tested; may work manually, but scripts are Linux/systemd focused |

---

## Tested hardware and platform notes

### Tested

- Raspberry Pi Zero 2 W running Raspberry Pi OS-style Linux.

### Expected to work better

- Raspberry Pi 4.
- Raspberry Pi 5.
- Other Debian/Ubuntu-like Linux boxes.
- Small x86 Linux mini-PCs.

A Pi Zero 2 W can run the dashboard, but a Pi 4 or Pi 5 is a much nicer target if you plan to use camera relay, logs, browser clients, and Ollama-related network calls heavily. Ollama itself should usually run on a stronger LAN machine, not on the Zero 2 W.

### Windows

Windows has **not** been tested. The backend is Python/FastAPI, so it might run with manual setup, but the included helper scripts, service installation, and process-management assumptions are aimed at Raspberry Pi OS / Linux / systemd.

---

## Feature overview

### Local dashboard

- FastAPI backend.
- Plain CSS and vanilla JavaScript frontend.
- Mobile-first responsive layout.
- Themeable UI.
- Collapsible dashboard sections.
- Saved dashboard accordion state per printer.
- Compact build/version chips in the header.
- `/health` and `/api/version` diagnostics.

### Printer discovery and pairing

- UDP Centauri discovery using Elegoo method `7000`.
- Verified-printer scan filtering so unrelated LAN devices are not shown as printer candidates.
- Manual printer add when discovery is blocked.
- Alphanumeric printer PIN/access-code fields.
- No prefilled default PIN.
- Printer serial/SN, access code, MQTT host/port, and command permissions stored per printer.

### Dashboard controls

Optional dashboard actions include:

- Light toggle.
- Pause print.
- Resume print.
- Cancel print.
- Camera wake/enable.
- Speed preset selection.
- Manual camera analysis.

Command buttons are controlled by per-printer safety settings. Dangerous commands remain gated so an accidental phone tap does not become a tiny disaster opera.

### Camera relay

- Keeps one upstream MJPEG camera connection to the printer.
- Serves dashboard clients from local relay/fanout endpoints.
- Provides cached latest frame for Portal AI and feedback capture.
- Helps prevent multiple browser tabs and AI checks from dogpiling the printer camera endpoint.

### Portal AI

- Telemetry/rule-based print health checks.
- Optional Ollama vision analysis.
- Local image heuristics for dark frames, contrast, fine-edge/stringing-like changes, and stale/frozen-looking frames.
- Active-print-only monitoring so idle printers do not waste cycles or create meaningless warnings.
- Advisory-only: no automatic pause/cancel in this version.

### Feedback-aware AI review

- Looks Good / Looks Bad / False Alarm buttons.
- Fresh camera frame capture on feedback click, with cached-frame fallback.
- Feedback interpreted into true positive / false positive / false negative / true negative.
- Same-print suppression for repeated low/severity false alarms.
- Feedback and frame data saved locally for later review/tuning.

### Stock portal bridge

- Bundled stock Elegoo-style portal page.
- Local MQTT-over-WebSocket bridge.
- Fullscreen portal route.
- Portal camera rewrite shim that tries to route embedded camera views through cc2-dash's camera relay.

### File and filament tools

- File Manager can list stock-style printer files, USB files, print history, and video records where firmware supports it.
- Timelapse export/download helpers are included, but printer firmware may not reliably generate/export videos.
- Filament Manager can display and control CANVAS/MMS filament slots using stock command shapes.
- Filament load/unload/edit controls are idle-only.

### Themes

Built-in themes include:

- Octo Dark Blue.
- Amber Terminal.
- Mainsail-ish Dark.
- Carbon Glass.
- Toxic Green Lab.
- Blood Red Terminal.
- Elegoo Dark.
- Klipper Blue.
- OLED Mono.
- Cyberpunk Magenta.
- High Contrast.

Theme preview cards are available in first-run setup and Settings.

---

## What cc2-dash does not do

This matters, so here it is without the marketing fog machine:

- It does **not** make the printer safe to leave unattended.
- It does **not** replace the stock Elegoo portal.
- It does **not** guarantee failure detection.
- It does **not** automatically pause/cancel prints from AI decisions in this version.
- It does **not** harden your LAN or provide production-grade authentication.
- It does **not** fix firmware features that are broken in the stock portal itself.

Use it as a local dashboard, helper, and experiment platform.

---

## Install from GitHub on Raspberry Pi OS

These instructions assume Raspberry Pi OS, Debian, Ubuntu, or a similar apt-based Linux system.

### 1. Update the Pi and install basic tools

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

### 2. Clone the repository

Clone the current project repository:

```bash
git clone https://github.com/merberg-ai/cc2-dash.git
cd cc2-dash
```

### 3. Make helper scripts executable

```bash
chmod +x install.sh run.sh uninstall.sh
```

### 4. Install Python dependencies

```bash
./install.sh
```

The installer will:

1. Check for Python 3.
2. Create `.venv/` if needed.
3. Upgrade `pip`, `setuptools`, and `wheel`.
4. Install packages from `requirements.txt`.
5. Create the local `data/` folder.

Dependencies currently include:

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

---

## Run manually

Start the app:

```bash
./run.sh
```

Open from another device on the same LAN:

```text
http://<pi-ip>:8088/
```

Example:

```text
http://192.168.1.50:8088/
```

To run on a different port:

```bash
CC2_PORT=8090 ./run.sh
```

---

## Install as a systemd service

For always-on use, install cc2-dash as a background service:

```bash
./install.sh --service --port=8088
```

Useful commands:

```bash
sudo systemctl status cc2-dash --no-pager
sudo systemctl restart cc2-dash
sudo systemctl stop cc2-dash
sudo journalctl -u cc2-dash -f
```

The service runs:

```bash
python -m uvicorn cc2_dash.main:app --host 0.0.0.0 --port 8088
```

---

## Update from GitHub

From the project folder:

```bash
git pull
./install.sh
```

If installed as a service:

```bash
sudo systemctl restart cc2-dash
```

If running manually, stop the old process and restart:

```bash
./run.sh
```

Your local runtime data lives in `data/`. Do not delete it unless you want to reset printers, logs, AI feedback, learned AI profiles, and settings.

If you previously installed the older `cc2-dash-lite.service`, the installer now attempts to stop, disable, and remove that legacy service so it does not compete with `cc2-dash.service` for the same port.

---

## First-run setup

When no valid printer is configured, cc2-dash opens the setup wizard.

Setup flow:

| Step | Purpose |
|---:|---|
| 1 | Find printers using verified Centauri discovery |
| 2 | Add a printer manually if discovery fails |
| 3 | Pick theme and font preferences |
| 4 | Configure LAN access allowlist |
| 5 | Configure Portal AI and optional Ollama vision |
| 6 | Review and launch dashboard |

The scanner only shows devices that answer the expected Centauri discovery probe. Routers, phones, smart plugs, and random LAN web servers should stay hidden.

Printer settings saved during setup:

```text
Printer display name
Printer host/IP
Printer serial/SN
Printer PIN/access code
MQTT port, usually 1883
Default printer selection
Command permission flags
```

---

## Using the dashboard

Primary pages:

| Page | Description |
|---|---|
| **Dash** | Main printer view with status, camera, quick actions, Portal AI, and connection info. |
| **Portal** | Stock Elegoo portal bridge/fallback. |
| **Kiosk** | Camera-first fullscreen display for tablets or spare monitors. |
| **Files** | Optional file/history/timelapse helper page. Hidden by default. |
| **Filament** | Optional CANVAS/MMS filament manager. Hidden by default. |
| **Settings** | Printer Manager, themes, menu visibility, quick actions, access, camera relay, AI settings. |
| **Logs** | Filterable runtime log viewer. |

The **Files**, **Filament**, and **Kiosk** navigation items can be shown or hidden in:

```text
Settings → Menu / Features
```

---

## Printer Manager

Open:

```text
Settings → Printer Manager
```

Available actions:

- Scan for verified Centauri printers.
- Add a printer manually.
- Edit printer name, IP/host, serial, access code, and MQTT port.
- Enable or disable printer entries.
- Choose the default printer.
- Enable/disable normal commands.
- Enable/disable dangerous commands.
- Remove old printer entries.

Command permissions are intentionally separate from pairing. You can monitor a printer while keeping control buttons locked down.

---

## Camera Relay / stream protection

The CC2 camera can get cranky when too many things connect to it directly. The Camera Relay reduces that load.

How it works:

1. cc2-dash opens one upstream MJPEG connection to the printer camera.
2. The latest frame is cached in memory.
3. Browser clients receive a local MJPEG stream from the dashboard server.
4. Portal AI and feedback capture use the cached/latest frame instead of opening extra direct camera connections.
5. The stock portal shim tries to rewrite embedded camera URLs through the relay.

Useful endpoints:

```text
GET  /api/printers/<printer_id>/camera/stream
GET  /api/printers/<printer_id>/camera/snapshot.jpg
GET  /api/printers/<printer_id>/camera/latest.jpg
GET  /api/printers/<printer_id>/camera/status
GET  /api/camera/status
POST /api/printers/<printer_id>/camera/restart
```

Recommended default: relay enabled, start-on-boot enabled, portal rewrites enabled, direct fallback disabled unless debugging.

---

## Kiosk mode

Kiosk mode opens a minimal camera-first page intended for:

- Wall tablet.
- Spare phone.
- Browser tab on a shop monitor.
- Quick glance print display.

It can show:

- Camera relay state.
- Active printer.
- Active file.
- Print state: **IDLE** or **PRINTING**.
- Progress bar and percent.
- Estimated time remaining.
- Portal AI badge.

Settings live under:

```text
Settings → Kiosk Mode
```

The Kiosk nav item can be shown/hidden under:

```text
Settings → Menu / Features
```

---

## Portal AI and Ollama vision

Portal AI combines printer telemetry, local rules, optional camera heuristics, and optional Ollama vision output into an advisory status.

Current behavior:

- Runs only during active print jobs.
- Stands by when the printer is idle.
- Tracks stale status, printer error states, pause/error/fail states, stuck progress, temp sanity, filament status hints, and camera/vision issues.
- Uses Ollama vision only when enabled and when an active print is detected.
- Does not automatically pause/cancel jobs.

Common checks:

- Printer connected/reachable.
- MQTT status freshness.
- Error/fail/emergency/stopped states.
- Paused state warning.
- Stuck progress timer.
- Hotend/bed target sanity during active print.
- Filament sensor hints.
- Camera availability hints.
- Dark/low-contrast frame checks.
- Fine-edge/stringing-like frame checks.
- Vision model classification.

Ollama settings live under:

```text
Settings → Portal AI
```

Typical Ollama URL:

```text
http://192.168.1.24:11434
```

Related controls:

| Control | Purpose |
|---|---|
| **Load Models** | Fetch installed Ollama models from `/api/tags`. |
| **Test** | Check that the selected model is reachable. |
| **Pull** | Request model download through Ollama. |
| **Analyze Camera Now** | Manually trigger a one-shot vision check during an active print. |
| **Treat benign uncertainty as OK** | Downgrade uncertain/no-evidence responses instead of warning loudly. |

---

## AI feedback and false-alarm suppression

Feedback buttons:

- **Looks Good**
- **Looks Bad**
- **False Alarm**

Feedback records are saved to:

```text
data/ai_feedback.jsonl
data/ai_feedback_frames/<printer_id>/
data/ai_feedback_suppressions.json
data/ai_learning.sqlite3
```

When feedback is clicked, cc2-dash tries to capture a fresh frame. If that fails, it falls back to the latest cached frame.

Feedback is interpreted against what Portal AI believed at the time:

| AI state | User feedback | Interpreted outcome |
|---|---|---|
| Warning | Looks Bad | True positive |
| Warning | Looks Good / False Alarm | False positive |
| OK | Looks Bad | False negative |
| OK | Looks Good | True negative |

False-positive feedback can create a temporary suppression for similar low/severity warnings on the same active print. This helps stop repeated “same thing again” warnings without changing your manual heuristic thresholds.

Review endpoints:

```text
GET /api/ai/feedback/recent
GET /api/ai/feedback/stats
GET /api/ai/feedback/suppressions
```

Manual threshold values remain manual. Feedback suppression does not silently rewrite your dark-frame or fine-edge thresholds.

---

## Persistent AI learning

cc2-dash now includes a lightweight SQLite-backed learning foundation for Portal AI feedback. The goal is long-term tuning without turning the Pi into a tiny screaming database furnace.

Files used:

```text
data/ai_feedback.jsonl
data/ai_feedback_frames/<printer_id>/
data/ai_learning.sqlite3
```

How it works in this version:

1. Feedback is still written to the human-readable JSONL audit log.
2. The same feedback is mirrored into `data/ai_learning.sqlite3` as structured samples.
3. Samples are grouped per printer.
4. Rebuild endpoints calculate per-printer learning profiles, outcome counts, normal baselines, and suggested threshold modifiers.
5. Manual threshold settings are not overwritten.
6. Default mode is `suggest_only`, so learned modifiers are calculated but not applied to live detection.
7. Portal AI remains advisory-only and does not pause, cancel, resume, load/unload filament, or control jobs automatically.

Learning modes under `portal_ai` config:

| Mode | Behavior |
|---|---|
| `off` | Store feedback but ignore learning suggestions. |
| `suggest_only` | Calculate suggested modifiers and expose them through the API; live detection uses manual settings. |
| `auto_adjust_safe` | Apply small bounded modifiers to live thresholds. Manual settings remain unchanged. |

Current bounds/defaults:

| Setting | Default |
|---|---:|
| `ai_learning_min_samples` | `8` |
| `ai_learning_min_false_positives` | `4` |
| `ai_learning_min_false_negatives` | `2` |
| `ai_learning_max_dark_luma_adjustment` | `8` |
| `ai_learning_max_edge_density_adjustment` | `0.05` |
| `ai_learning_max_required_bad_checks_adjustment` | `1` |

The learning database uses Python's built-in `sqlite3` module with WAL mode, normal sync, a short busy timeout, and no image blobs. Images stay on disk; SQLite stores paths and metrics only.

> [!IMPORTANT]
> v1.2.34 adds the backend foundation and APIs. Full Settings UI panels, feedback reason chips, and live effective-threshold integration are planned follow-up work.

---

## File Manager

The File Manager is available but hidden by default.

Enable it here:

```text
Settings → Menu / Features → File Manager
```

Sections:

| Section | Purpose |
|---|---|
| **Printer Files** | Stock-style local printer file list. |
| **USB Drive** | Stock-style USB/u-disk file list with folder navigation. |
| **Print History** | Print history records where firmware reports them. |
| **Video List** | Timelapse/video records derived from stock history/video metadata. |

Stock command IDs used include:

```text
1036  Get history task
1037  Get history task detail
1038  Delete history
1044  Get file list
1045  Get file thumbnail
1046  Get file detail
1047  Delete file
1051  Get/export timelapse video list
```

> [!CAUTION]
> The stock firmware may not reliably generate/export timelapse videos even when the stock portal shows the UI. cc2-dash includes a proxy for the stock `/download` flow, but it cannot fix firmware-side export failures.

---

## Filament Manager / CANVAS controls

The Filament Manager is available but hidden by default while real-printer behavior is tested.

Enable it here:

```text
Settings → Menu / Features → Filament Manager
```

Current CANVAS/MMS features:

- Read CANVAS status.
- Display filament slot cards.
- Display color swatches.
- Display filament metadata where firmware reports it.
- Display filament sensor state with improved normalization.
- Slot layout order: **1, 4, 2, 3**.
- Load/feed selected slot.
- Unload selected slot.
- Edit selected filament profile.
- Toggle Auto Filament Refill.
- Refresh from printer after edit/load/unload/refill changes.
- Lock load/unload/edit controls unless the printer is idle.

Stock command IDs used include:

```text
2001  Load/feed filament
2002  Unload filament
2003  Edit CANVAS filament info
2004  Auto Filament Refill
2005  Get CANVAS status
1055  Set mono filament info
1061  Get mono filament info
```

> [!WARNING]
> Filament load/unload physically moves filament. Keep eyes on the printer while testing. The UI blocks these actions during active prints, but firmware behavior still deserves adult supervision and possibly a stern look.

---

## Stock Elegoo portal bridge

Routes:

```text
/portal-fullscreen
/portal
/elegoo/octo_portal.html
```

Local MQTT WebSocket bridge:

```text
/ws/mqtt/<printer_id>
```

The bridge shuttles browser WebSocket MQTT frames to the printer's MQTT port, usually `1883`.

The stock portal remains the fallback/reference view. If a cc2-dash feature is experimental or firmware-specific, compare behavior against the stock portal.

---

## Themes and appearance

Themes live in:

```text
cc2_dash/themes.py
```

Current built-in themes:

| Theme | Style |
|---|---|
| Octo Dark Blue | Clean dark blue dashboard |
| Amber Terminal | Warm terminal / CRT-ish style |
| Mainsail-ish Dark | Familiar printer-dashboard dark UI |
| Carbon Glass | Dark translucent glass panels |
| Toxic Green Lab | Green terminal/lab console vibe |
| Blood Red Terminal | Red horror-terminal look |
| Elegoo Dark | Closer to stock portal colors |
| Klipper Blue | Blue printer-dashboard theme |
| OLED Mono | Minimal black/white high readability |
| Cyberpunk Magenta | Neon magenta/cyan chaos, in a good way |
| High Contrast | Accessibility-focused contrast |

Theme preview cards are available in:

```text
Setup wizard → UI step
Settings → Theme + Fonts
```

Font stacks are CSS-based. No font files are bundled.

---

## Logs and diagnostics

Logs page:

```text
/logs
```

Persisted log file:

```text
data/logs/system.jsonl
```

Common log sources:

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

Useful diagnostics:

```text
GET /health
GET /api/version
GET /api/status
GET /api/ai/monitor
GET /api/camera/status
```

---

## Safety gates and command behavior

Per-printer command permissions are configured in:

```text
Settings → Printer Manager
```

There are two important permission layers:

| Permission | Meaning |
|---|---|
| Commands enabled | Allows normal printer command actions. |
| Dangerous commands enabled | Allows riskier actions such as cancel/delete/start-style operations. |

Current command mapping summary:

| Feature | Method / behavior |
|---|---|
| File listing/history | `1036`, `1037`, `1044`, `1046`, `1051` |
| File/history delete | `1038`, `1047` |
| Filament Manager | `2001`, `2002`, `2003`, `2004`, `2005`, `1055`, `1061` |
| Light toggle | `1029` |
| Pause print | `1021` |
| Resume print | `1023` |
| Cancel print | `1022` |
| Camera wake/enable | `1042` / `1054` |
| Speed preset | `1031` with mode `0-3` |
| Analyze Camera Now | Server-side advisory vision check only |

Speed preset modes:

| Mode | Label |
|---:|---|
| `0` | Silent |
| `1` | Balanced |
| `2` | Sport |
| `3` | Ludicrous / Frenzy |

Again: Portal AI does not automatically trigger pause/cancel in this version.

---

## Configuration and data paths

Default runtime data folder:

```text
./data/
```

Important files:

```text
data/config.json
data/logs/system.jsonl
data/vision/<printer_id>/latest.jpg
data/ai_feedback.jsonl
data/ai_feedback_frames/<printer_id>/
data/ai_feedback_suppressions.json
data/ai_learning.sqlite3
```

Useful environment variables:

```bash
export CC2_DATA_DIR=/path/to/data
export CC2_CONFIG=/path/to/config.json
export CC2_PORT=8088
```

Default access allowlist:

```text
192.168.1.0/24
localhost
```

Configure this during setup or later in Settings. Keep it restricted to trusted LAN ranges.

---

## Useful API endpoints

General:

```text
GET /health
GET /api/version
GET /api/status
```

Camera:

```text
GET  /api/printers/<printer_id>/camera/stream
GET  /api/printers/<printer_id>/camera/latest.jpg
GET  /api/printers/<printer_id>/camera/snapshot.jpg
GET  /api/printers/<printer_id>/camera/status
POST /api/printers/<printer_id>/camera/restart
```

AI / vision:

```text
GET  /api/ai/monitor
GET  /api/printers/<printer_id>/ai/status
POST /api/printers/<printer_id>/ai/check-now
POST /api/printers/<printer_id>/ai/feedback
GET  /api/ai/feedback/recent
GET  /api/ai/feedback/stats
GET  /api/ai/feedback/suppressions
GET  /api/ai/learning/status
POST /api/ai/learning/rebuild
POST /api/ai/learning/reset
GET  /api/printers/<printer_id>/ai/learning
POST /api/printers/<printer_id>/ai/learning/rebuild
POST /api/printers/<printer_id>/ai/learning/reset
GET  /api/printers/<printer_id>/ai/learning/samples
GET  /api/printers/<printer_id>/vision/status
POST /api/printers/<printer_id>/vision/check-now
GET  /api/printers/<printer_id>/vision/latest.jpg
GET  /api/vision/models
POST /api/vision/pull
```

Stock portal bridge:

```text
GET /portal
GET /portal-fullscreen
GET /elegoo/octo_portal.html
WS  /ws/mqtt/<printer_id>
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
sudo systemctl status cc2-dash --no-pager
sudo journalctl -u cc2-dash -f
```

### Browser cannot reach the dashboard

Check:

1. Pi IP address.
2. Port, default `8088`.
3. Firewall/router rules.
4. cc2-dash access allowlist.
5. Whether the service is running.

### Scan does not find the printer

Try:

1. Confirm the printer is powered on.
2. Confirm the Pi and printer are on the same LAN/subnet.
3. Try direct printer IP in setup/manual add.
4. Confirm printer serial/SN and access code.
5. Check **Logs → scanner**.

The scan UI only shows verified Centauri responses. A generic open web port does not count.

### Stock portal opens but does not control the printer

Check:

1. Printer serial/SN.
2. PIN/access code.
3. MQTT port, usually `1883`.
4. Printer Manager command toggles.
5. Browser console.
6. **Logs → command**.

### Camera stream is flaky

Try:

1. Enable Camera Relay.
2. Restart the camera relay from Settings or API.
3. Close other direct camera viewers.
4. Avoid opening printer `:8080` directly in multiple tabs.
5. Check `/api/camera/status`.

### Ollama model list does not load

Check:

1. Ollama is running.
2. The URL includes protocol and port, for example `http://192.168.1.24:11434`.
3. The Pi can reach the Ollama host.
4. The selected vision model is installed.

### Portal AI does nothing while idle

That is expected. Current behavior is active-print-only monitoring. The loop still wakes lightly to check status so it can resume when a print starts, but it avoids heavy AI/vision work while idle.

### File Manager video download/export fails

If the stock Elegoo portal also fails, the problem is probably firmware-side. cc2-dash includes the stock-style command path and download proxy, but it cannot force the printer firmware to generate a missing timelapse file.

### Filament sensor says unknown

The app normalizes several known stock/raw sensor paths, but firmware may report different shapes depending on mode, CANVAS state, or printer firmware. Check **Logs → filament** and compare against the stock portal.

---

## Known limitations

- Private/hobby LAN use only; not production-hardened.
- Windows is untested.
- AI/vision monitoring is advisory only.
- AI does not automatically pause/cancel jobs in this version.
- Vision checks can produce false positives and false negatives.
- Camera quality, lighting, glare, focus, and angle matter a lot.
- Firmware response shapes may vary by version.
- Timelapse/video export may not work even in the stock portal.
- File Manager and Filament Manager remain firmware-sensitive.
- No frontend build is required, but this also means the UI is intentionally simple and dependency-light.

---

## Uninstall

Remove the service while keeping local data:

```bash
./uninstall.sh
```

Remove service plus `.venv` and `data/`:

```bash
./uninstall.sh --purge
```

> [!CAUTION]
> `--purge` deletes local configuration, logs, vision frames, and AI feedback data.

---

## Project layout

```text
cc2-dash/
├── cc2_dash/
│   ├── __init__.py
│   ├── ai.py
│   ├── build_info.py
│   ├── camera_proxy.py
│   ├── config.py
│   ├── feedback_learning.py
│   ├── logger.py
│   ├── printer_client.py
│   ├── scanner.py
│   ├── themes.py
│   ├── vision.py
│   ├── cc2/
│   │   ├── client.py
│   │   ├── commands.py
│   │   ├── discovery.py
│   │   ├── runtime.py
│   │   └── state.py
│   └── elegoo_web/
│       ├── cc2dash-camera-shim.js
│       ├── cc2dash-shim.js
│       └── octo_portal.html
├── static/
│   ├── app.css
│   └── app.js
├── templates/
│   ├── base.html
│   ├── filaments.html
│   ├── files.html
│   ├── index.html
│   ├── kiosk.html
│   ├── logs.html
│   ├── portal.html
│   ├── settings.html
│   └── setup.html
├── install.sh
├── run.sh
├── uninstall.sh
├── requirements.txt
└── README.md
```

---

## Release notes

### v1.2.34 persistent AI learning foundation

- Added `cc2_dash/ai_learning_db.py` for lightweight SQLite setup, schema creation, feedback sample inserts, profile storage, event logging, reset helpers, and health checks.
- Added `cc2_dash/ai_learning.py` for structured sample extraction, outcome counts, per-printer profile rebuilds, normal baseline calculations, suggested modifier calculations, and effective-threshold summaries.
- Added `data/ai_learning.sqlite3` as a sidecar learning database. JSONL feedback logging remains intact.
- Feedback clicks now mirror structured samples into SQLite while preserving the existing JSONL audit trail and same-print false-alarm suppression behavior.
- Added learning status/rebuild/reset/sample APIs for global and per-printer use.
- Added `/health` AI learning database status.
- Added `portal_ai` config defaults for persistent learning, defaulting to `suggest_only`.
- Kept Portal AI advisory-only; no automatic pause/cancel/control behavior was added.
- Folded in project cleanup: README clone URL now points to `https://github.com/merberg-ai/cc2-dash.git`, internal runtime class renamed to `Cc2PrinterRuntime`, old `cc2-dash-lite.service` cleanup added, and backend `123456` access-code fallbacks removed.

### v1.2.33 dashboard metrics and G-code thumbnails

- Dashboard Print Status now attempts to populate **Filament Used** from additional stock/firmware field names including `totalFilamentUsed`, material weight, and filament length aliases. If firmware does not publish a usable value, the UI still shows `-` rather than inventing one.
- Expanded Print Status now shows layer progress when available, such as `120/450`.
- Added optional dashboard G-code thumbnail preview for the active file. The preview only appears when the printer returns a usable thumbnail image.
- Clicking the thumbnail opens a larger themed glass modal with a close button.
- Added **Settings → Dashboard Layout → G-code thumbnail preview** to show/hide the thumbnail section.

### v1.2.32 crt themes

- Added two new built-in retro monitor themes: **Retro CRT Blue-Gray** and **Green Phosphor CRT**.
- Both themes use the built-in **Retro CRT** font stack with scanline/glow styling for an old-monitor feel.
- Theme preview cards in Settings and first-run setup now include the two new CRT-style themes.

### v1.2.31 theme expansion

- Added six built-in themes: **Toxic Green Lab**, **Blood Red Terminal**, **Elegoo Dark**, **Klipper Blue**, **OLED Mono**, and **Cyberpunk Magenta**.
- Added clickable theme preview cards to Settings and first-run setup.
- Existing theme/font override behavior is preserved.

### v1.2.30 filament polish

- Reordered CANVAS slot display to **1, 4, 2, 3**.
- Added refresh-after-action behavior for edit/load/unload/Auto Refill.
- Locked load/feed, unload, and edit controls to idle-only behavior.
- Added backend rejection while printing or during filament/extruder operation states.
- Improved Auto Filament Refill behavior using the stock payload.
- Improved filament sensor normalization.
- Made firmware command failures louder instead of reporting fake success.

### v1.2.29 filament CANVAS controls

- Added stock-shaped CANVAS status, load/feed, unload, edit, and Auto Refill controls.
- Added filament color swatches and richer slot metadata.
- Added mono-filament helper methods where firmware exposes them.

### v1.2.28 collapsed print state + filament hidden

- Collapsed Print Status header shows **IDLE** or **PRINTING** with compact progress.
- Filament nav item defaults hidden and can be re-enabled in Settings.

### v1.2.27 idle AI standby

- Normalized raw idle status code `Sub 0` to **Idle**.
- Added active-print detection.
- Portal AI/watchdog/vision monitoring now stands by when idle.

### v1.2.26 file manager hidden

- File Manager nav item defaults hidden because firmware timelapse/export behavior appears inconsistent.
- Existing stock-style file manager work remains available for later testing.

### v1.2.25 timelapse download proxy

- Added cc2-dash timelapse download proxy through the printer stock `/download` handler.
- Converted export-returned video paths/tokens into dashboard download links.

### v1.2.24 stock-style file manager

- Reworked File Manager around stock command shapes.
- Added Printer Files, USB Drive, Print History, and Video List sections.

### v1.2.23 feedback learning

- Added fresh-frame feedback capture.
- Added true/false positive/negative interpretation.
- Added current-print false-alarm suppression for similar low/severity warnings.
- Improved feedback stats and suppression API.

### v1.2.22 alphanumeric access codes

- Updated setup/settings PIN fields to allow letters and numbers.
- Removed prefilled default PIN.
- Backend rejects blank access codes.

### v1.2.21 setup copy cleanup

- Simplified first-run setup copy.
- Reduced first card to progress-only header treatment.

### v1.2.20 kiosk camera warm-up

- Kiosk uses faster cached status.
- Improved camera placeholder/stream loading behavior.

### v1.2.19 kiosk mode

- Added hideable Kiosk nav item and camera-first fullscreen page.

### v1.2.18 AI header status

- Added compact Portal AI status pill to collapsed AI Info header.

### v1.2.17 collapsed progress

- Added compact progress bar to collapsed Print Status header.

### v1.2.16 dashboard section split

- Split Camera, Print Status, AI Info, Quick Actions, and Connection into clearer collapsible sections.

### v1.2.15 dashboard accordion polish

- Added saved dashboard accordion state.

### v1.2.14 mobile header/settings cleanup

- Improved mobile header build chips.
- Reworked Settings into collapsible panels with global Save All / Cancel controls.

### v1.2.13 vision sanity + service cleanup

- Improved benign-uncertainty handling for Ollama vision.
- Improved install/uninstall systemd cleanup.

### v1.2.12 portal navigation fix

- Portal nav opens fullscreen stock portal in a new tab instead of nesting wrappers.

### v1.2.11 camera relay

- Added MJPEG relay/fanout, cached latest-frame endpoints, and portal camera rewrite shim.

### v1.2.10 documentation/source cleanup

- Reworked documentation and removed informal placeholder references.

### v1.2.1 build metadata

- Added version/commit/branch build metadata in header, `/api/version`, and `/health`.

### v1.2.0 background watchdog

- Added background Portal AI monitoring loop and `/api/ai/monitor`.

### v1.0.0 stable baseline

- First-run setup, stock portal bridge, mobile dashboard shell, themes, feature toggles, install scripts, and early file hooks.

---

## Development checks

No frontend build step is required for normal use.

After edits, useful checks are:

```bash
python -m compileall cc2_dash
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

The Node check is optional and only verifies JavaScript syntax if Node is installed.
