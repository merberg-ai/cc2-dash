# cc2-dash

![Version](https://img.shields.io/badge/version-1.2.63-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20%2F%20Linux-green)
![Use](https://img.shields.io/badge/use-private%20hobbyist%20LAN-orange)

**cc2-dash** is a lightweight local dashboard and portal shell for the **Elegoo Centauri Carbon 2 / CC2** ecosystem. It gives you a clean LAN dashboard, printer discovery and pairing, camera relay/fanout, a stock Elegoo portal bridge, optional Ollama-powered visual monitoring, feedback-aware AI review tools, G-code upload staging, kiosk mode, stock-style control tools, and a themeable mobile-friendly UI. Experimental File Manager and Filament Manager code is retained in the source tree but locked off for this public test build; the Control page remains enabled with runtime safety gates.

It is designed for a Raspberry Pi-style board sitting on your trusted home network. It is intended as a printer-room companion dashboard, not an enterprise print-farm controller.

> [!WARNING]
> **Private, home, hobbyist use only.** cc2-dash is not designed, tested, or recommended for production environments, commercial print farms, safety-critical workflows, unattended remote operation, or any situation where missed detection, a failed command, or an incorrect AI result could cause damage. Keep physical access to your printer and use the stock printer controls as the final authority.

> [!IMPORTANT]
> This is an unofficial project. It is not affiliated with, endorsed by, or supported by Elegoo, OctoEverywhere, or any printer vendor. Firmware behavior can change. Some stock command paths behave differently across firmware versions.

> [!NOTE]
> In this version, **Failure Detection can optionally pause a print after a high-risk warning countdown**. Auto-pause is off by default, has a configurable countdown/cancel window, uses a shared pause-permission gate, and performs a fresh telemetry/vision recheck immediately before sending `PAUSE_PRINT`. Cancel print remains locked behind manual controls; AI never cancels, resumes, loads/unloads filament, or overrides the stock printer controls.

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
- [AI Training review page](#ai-training-review-page)
- [Upload page](#upload-page)
- [File Manager](#file-manager)
- [Filament Manager / CANVAS controls](#filament-manager--canvas-controls)
- [Control page](#control-page)
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
1.2.63 timelapse-export-confirmation
1.2.62 async-timelapse-export
1.2.61 roi-feedback-mobile-cache-fix
1.2.60 roi-missed-failure-feedback
```

Major current capabilities:

| Area | Status |
|---|---|
| Printer discovery / pairing | Working, verified Centauri discovery filtering |
| Dashboard status | Working, mobile-first, active/idle aware |
| Stock portal bridge | Working as fallback/reference portal |
| Camera relay | Working, reduces direct camera connection pileups |
| Kiosk mode | Working, camera-first fullscreen view |
| Failure Detection telemetry checks | Working, with optional guarded auto-pause off by default |
| Ollama vision checks | Working, active-print-only by default |
| AI feedback dataset | Working, includes fresh-frame capture, missed-failure ROI annotation/crops, optional reason chips, JSONL audit log, SQLite mirror/import, outcome interpretation, AI Training review/export tools |
| False-alarm suppression | Working for similar low/severity warnings on the same active print |
| Persistent AI learning | Working foundation plus Settings UI visibility and optional safe auto-adjustment of live vision thresholds |
| Upload page | Working, stages local `.gcode` files in cc2-dash, extracts metadata/thumbnails where possible, then uploads or uploads-and-prints |
| File Manager | Experimental code retained, disabled and locked off for this public test build; dev branch includes async timelapse export, confirmed generated/download-ready status before download, and friendly timelapse composing status labels |
| Filament Manager / CANVAS | Experimental code retained, disabled and locked off for this public test build |
| Control page | Enabled, stock-portal-style controls with offline/active-print lockouts, command permissions, fans, speed, light, jog/home, and bed/extruder temperature controls |
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
- Configurable top navigation visibility for Portal, Upload, Control, Kiosk, AI Training, and Logs. File Manager and Filament Manager are locked off in this public test build.
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

Command buttons are controlled by per-printer safety settings. Dangerous commands remain gated to reduce accidental activation from touch devices.

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
- Optional guarded auto-pause can pause on high-risk failures after a configurable warning countdown. Auto-pause is off by default and never cancels a print.

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

### Experimental file and filament tools

The File Manager and Filament Manager are still present in the codebase, but they are disabled and locked off for this public test build. This keeps the community release focused on the stable dashboard, portal, camera relay, Upload page, Control page, Failure Detection, kiosk, settings, logs, and AI training workflows while the most firmware-sensitive file/history/CANVAS tools get more validation.

The release gate is configured in:

```text
cc2_dash/config.py
```

Current gate switch:

```python
COMMUNITY_RELEASE_EXPERIMENTAL_LOCKS = True
```

Current locked feature keys:

```python
EXPERIMENTAL_FEATURE_LOCKS = {
    "file_manager_enabled": ...,
    "filament_manager_enabled": ...,
}
```

When the master lock is enabled, cc2-dash forces those feature flags to `false` during config load/save, disables their Settings toggles, and blocks direct page/API access with a disabled-feature page or `403` response. **Control is intentionally not listed in `EXPERIMENTAL_FEATURE_LOCKS`**, so it remains available while still being protected by command permissions, offline checks, and active-print lockouts.

When the file/filament lock is removed in a future build, these retained tools include:

- File Manager support for stock-style printer files, USB files, print history, and video records where firmware supports it.
- Timelapse export/download helpers, subject to firmware behavior.
- CANVAS/MMS filament slot display and command helpers.
- Idle-only filament load/unload/edit controls.

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

Important boundaries:

- It does **not** make the printer safe to leave unattended.
- It does **not** replace the stock Elegoo portal.
- It does **not** guarantee failure detection.
- It does **not** cancel, resume, load/unload filament, jog axes, set heaters, or override stock controls from AI decisions. Optional auto-pause is off by default and only sends pause after an explicit opt-in countdown.
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
| **Upload** | Stage `.gcode` files in cc2-dash, review parsed metadata/thumbnails, then upload or upload-and-print. |
| **Control** | Stock-portal-style jog/home, light, fans, speed, and bed/extruder temperature controls with safety lockouts. |
| **Files** | Experimental file/history/timelapse helper page. Locked off in this public test build. |
| **Filament** | Experimental CANVAS/MMS filament manager. Locked off in this public test build. |
| **Settings** | Printer Manager, themes, menu visibility, quick actions, access, camera relay, AI settings. |
| **Logs** | Filterable runtime log viewer. |

The **Portal**, **Upload**, **Control**, **Kiosk**, **AI Training**, and **Logs** navigation items can be shown or hidden in:

```text
Settings → Menu / Features
```

The **Files** and **Filament** rows remain visible in Settings as locked release-gated features while `COMMUNITY_RELEASE_EXPERIMENTAL_LOCKS` is enabled.

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
- Does not cancel jobs automatically. Optional high-risk auto-pause is available only when explicitly enabled.

Common checks:

- Printer connected/reachable.
- MQTT status freshness.
- Error/fail/emergency/stopped states.
- Paused state warning.
- Stuck progress timer.
- Hotend/bed target sanity during active print.
- Filament sensor hints.
- Printer exception-code decoding, such as `1252 — Extruder unload failure / unload timeout`, when the firmware reports known exception IDs.
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
- **Report Missed Failure** — captures a frozen camera frame and lets you draw a mobile-friendly box around the failed area.

Feedback records are saved to:

```text
data/ai_feedback.jsonl
data/ai_feedback_frames/<printer_id>/
data/ai_feedback_suppressions.json
data/ai_learning.sqlite3
```

When feedback is clicked, cc2-dash tries to capture a fresh frame. If that fails, it falls back to the latest cached frame. After the fast click is saved, an optional reason-chip panel can tag why the feedback was given, such as normal supports, purge tower, spaghetti/stringing, detached print, low light but visible, or a custom note.

For missed localized failures, **Report Missed Failure** opens a frozen snapshot with a touch-safe SVG overlay. Draw one box around the specific failed area, choose the failure type, and save. The backend stores normalized ROI coordinates plus a full-frame image, tight ROI crop, and padded context crop under `data/ai_feedback_frames/<printer_id>/`. This is review/training evidence only in v1.2.60; it does not change auto-pause or cancel permissions yet.

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
GET /api/ai/learning/samples
GET /api/ai/learning/samples/<sample_id>/frame
GET /api/ai/learning/samples/<sample_id>/roi-frame
POST /api/ai/learning/import-jsonl
GET /api/printers/<printer_id>/ai/learning/samples
POST /api/printers/<printer_id>/ai/feedback/frame
POST /api/printers/<printer_id>/ai/feedback/reason
```

Manual threshold values remain manual. Feedback suppression does not silently rewrite your dark-frame or fine-edge thresholds.

---

## Persistent AI learning

cc2-dash now includes a lightweight SQLite-backed learning foundation for Portal AI feedback. The goal is long-term tuning while keeping resource use reasonable on Raspberry Pi-class hardware.

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
5. Settings → Portal AI shows the AI Feedback Learning controls and profile cards.
6. Manual threshold settings are not overwritten.
7. Default mode is `suggest_only`, so learned modifiers are calculated and shown but not applied to live detection unless you explicitly switch to `auto_adjust_safe`.
8. In `auto_adjust_safe`, bounded learned modifiers are applied only to the live in-memory vision check thresholds; your manual settings are still not overwritten.
9. Failure Detection auto-pause is opt-in. When enabled, high-risk active-print failures can arm a countdown and send `PAUSE_PRINT` if not cancelled. Cancel print, resume, load/unload filament, and other job-control actions remain manual.

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

Settings → Portal AI now includes **AI Feedback Learning** controls for:

- enabling/disabling persistent learning;
- switching between `off`, `suggest_only`, and `auto_adjust_safe`;
- tuning minimum sample requirements and clamp bounds;
- choosing which modifier types may be suggested;
- rebuilding profiles;
- resetting learned tuning without deleting JSONL feedback;
- viewing per-printer sample counts, outcomes, baselines, reasons, and manual/suggested/applied/effective thresholds;
- reviewing recent feedback samples with filters for printer, label, outcome, metrics, reasons, and captured feedback frames;
- importing/backfilling older `data/ai_feedback.jsonl` audit rows into SQLite with duplicate skipping and optional profile rebuild.

> [!IMPORTANT]
> Learned effective thresholds are wired into live vision checks only when `auto_adjust_safe` is explicitly selected. `off` and `suggest_only` remain advisory/no-op for live scoring. Feedback reason chips enrich future samples but still do not let AI control print jobs automatically.

---


## AI Training review page

The `/ai-training` page is a lightweight local review console for Portal AI feedback samples. It is meant to make the SQLite learner inspectable without needing to SSH into the Pi or manually query the database.

Current AI Training tools include:

- Filter feedback samples by printer, label, and interpreted outcome.
- Review captured feedback frame thumbnails when available.
- Edit/relabel a sample's feedback label, interpreted outcome, and reason note.
- Delete bad SQLite training samples while keeping the JSONL audit log and captured frame files intact.
- Export a ZIP dataset containing public sample metadata, raw JSONL rows, and optionally captured frame files.

The page does **not** train an Ollama model, upload data, or send commands to the printer. It only reviews and cleans local feedback/training records used by cc2-dash's lightweight heuristic learner.


## Upload page

The Upload page is enabled in this build and is meant for a cautious, review-before-send workflow.

Current upload flow:

1. Choose a local `.gcode` file.
2. cc2-dash stages the file under `data/staged_gcode_uploads/`.
3. The page shows upload progress while the browser sends the file to cc2-dash.
4. cc2-dash parses common slicer metadata, estimates motion bounds where possible, calculates hashes, and extracts embedded thumbnail images when available.
5. After review, you can send the file to the printer, or send it and request print start.

Useful staged-upload endpoints:

```text
GET  /upload
POST /api/uploads/stage
GET  /api/uploads/<upload_id>
GET  /api/uploads/<upload_id>/thumbnail
POST /api/printers/<printer_id>/uploads/<upload_id>/send
```

Compatibility/direct-upload endpoint retained for simpler clients:

```text
POST /api/printers/<printer_id>/files/upload
```

> [!CAUTION]
> Upload and especially upload-and-print are real printer actions. Keep command permissions restricted, verify the staged file details, and compare with the stock portal/slicer workflow if your firmware behaves differently.

## File Manager

The File Manager is **experimental and locked off** in this public test build. The source code and API handlers remain in place, but the menu toggle is disabled and direct access returns a disabled-feature page until the release gate is removed.

Sections:

| Section | Purpose |
|---|---|
| **Printer Files** | Stock-style local printer file list. |
| **USB Drive** | Stock-style USB/u-disk file list with folder navigation. |
| **Print History** | Print history records where firmware reports them. |
| **Video List** | Timelapse/video records derived from stock history/video metadata. Videos with status `1` must be exported/generated first; Download is enabled after export completes/status becomes ready. |

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

Timelapse export/download flow:

```text
1. Open Files → Video List.
2. Rows marked needs export/status 1 show Download disabled.
3. Tap Export. cc2-dash starts a backend export job and the UI shows Time-lapse video generating… instead of holding the browser request open.
4. The backend keeps polling the printer Video List until that row is actually reported as generated/download-ready, instead of trusting the initial export-command acknowledgement.
5. The frontend polls the lightweight cc2-dash job status until the backend confirms readiness or times out.
6. Once ready, refresh/download uses cc2-dash's proxied `/download` route so the printer PIN/internal URL is not exposed to the browser.
```

API helpers:

```text
POST /api/printers/<printer_id>/timelapse/export
GET  /api/printers/<printer_id>/timelapse/export/<job_id>
GET  /api/printers/<printer_id>/timelapse/download?file_name=<printer-file-token>
```

> [!CAUTION]
> The stock firmware may not reliably generate/export timelapse videos even when the stock portal shows the UI. cc2-dash now starts timelapse export as a backend job, shows **Time-lapse video generating…**, polls the printer Video List for confirmed generated/download-ready status, and only enables Download once a generated file is available. It still cannot fix firmware-side export failures.

---

## Filament Manager / CANVAS controls

The Filament Manager is **experimental and locked off** in this public test build. The source code and API handlers remain in place, but the menu toggle is disabled and direct access returns a disabled-feature page until CANVAS/MMS behavior is tested more broadly.

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
> Filament load/unload physically moves filament. Keep the printer supervised while testing. The UI blocks these actions during active prints, but firmware behavior still needs real-world validation.

---

## Control page

The Control page is enabled in this build. It is still treated as command-sensitive and firmware-specific, but it is no longer behind the public-release master lock.

Current control features:

- Compact live camera relay panel.
- Current printer connection/status summary.
- Live X/Y/Z position display.
- Stock-style X/Y jog wheel and Z jog rail.
- Home all axes, home X/Y, and individual X/Y/Z home buttons.
- Jog step buttons for **0.1mm**, **1mm**, **10mm**, and **30mm**.
- Print speed presets: **Silent 50%**, **Balanced 100%**, **Sport 130%**, and **Ludicrous 160%**.
- Model, Assistance/Auxiliary, and Case/Box fan controls using stock portal field names.
- Fan UI displays 0–100%, while outgoing method `1030` values are converted to the stock portal's 0–255 PWM-style scale.
- Light toggle using the same stock-style `power` payload as the dashboard light control.
- Current/target extruder and bed temperature display.
- Set extruder target temperature, bed target temperature, and turn either heater off.
- Compact themed temperature inputs populated from the current target/set values.
- Live status refresh that avoids overlapping refreshes and avoids overwriting fan/temp inputs while you are typing.

Stock command IDs used include:

```text
1026  Home axes, payload {homed_axes}
1027  Move/jog axes, payload {axes, distance}
1028  Set temperature, payload {extruder} or {heater_bed}
1029  Light toggle, payload {power}
1030  Set fan speed, payload {fan}, {aux_fan}, or {box_fan} using 0-255 values
1031  Set print speed mode, payload {mode}
```

Speed mode mapping:

| Mode | UI label |
|---:|---|
| `0` | Silent 50% |
| `1` | Balanced 100% |
| `2` | Sport 130% |
| `3` | Ludicrous / Frenzy 160% |

Temperature limits in the UI/backend:

| Heater | Range |
|---|---:|
| Extruder | `0–350°C` |
| Bed | `0–110°C` |

Safety behavior:

- All Control page commands are blocked while a print job is active.
- All Control page commands are blocked while the printer is offline/stale/connecting.
- Fan, speed, light, and temperature controls require **Commands enabled** for the printer.
- Jog and home controls require both **Commands enabled** and **Dangerous commands enabled**.
- The page can be hidden from the top navigation in Settings, but it is not part of the File/Filament master release lock.

> [!CAUTION]
> Movement, homing, fan, heater, and speed controls are real printer commands. Keep eyes on the machine. A browser UI should not be your only safety plan.

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
files
control
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
| Control page jog/home | `1026`, `1027` gated by dangerous-command permission |
| Control page fans/speed/light/temp | `1030`, `1031`, `1029`, `1028` gated by command permission |
| Control page fan value scaling | UI percent is converted to 0–255 stock portal fan values |
| Control page temperature | `1028` with `{extruder}` or `{heater_bed}` |
| Light toggle | `1029` |
| Upload staged G-code | Stage locally, then HTTP upload to printer; upload-and-print can request print start after transfer |
| Pause print | `1021` |
| Resume print | `1023` |
| Cancel print | `1022` |
| Camera wake/enable | `1042` / `1054` |
| Speed preset | `1031` stock mode payload on Control page; dashboard legacy speed action remains available |
| Analyze Camera Now | Server-side advisory vision check only |

Speed preset modes:

| Mode | Label |
|---:|---|
| `0` | Silent |
| `1` | Balanced |
| `2` | Sport |
| `3` | Ludicrous / Frenzy |

Again: Failure Detection auto-pause is opt-in and pause-only. It never sends cancel print automatically.

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
data/tmp_uploads/
data/staged_gcode_uploads/
data/staged_gcode_uploads/meta/
data/staged_gcode_uploads/thumbnails/
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
POST /api/printers/<printer_id>/ai/feedback/frame
POST /api/printers/<printer_id>/ai/feedback/reason
GET  /api/ai/feedback/frame
GET  /api/ai/feedback/recent
GET  /api/ai/feedback/stats
GET  /api/ai/feedback/suppressions
GET  /api/ai/learning/status
POST /api/ai/learning/rebuild
POST /api/ai/learning/reset
POST /api/ai/learning/import-jsonl
GET  /api/printers/<printer_id>/ai/learning
POST /api/printers/<printer_id>/ai/learning/rebuild
POST /api/printers/<printer_id>/ai/learning/reset
GET  /api/printers/<printer_id>/ai/learning/samples
GET  /api/ai/learning/samples/<sample_id>/roi-frame
GET  /api/printers/<printer_id>/vision/status
POST /api/printers/<printer_id>/vision/check-now
GET  /api/printers/<printer_id>/vision/latest.jpg
GET  /api/vision/models
POST /api/vision/pull
```

Upload:

```text
GET  /upload
POST /api/uploads/stage
GET  /api/uploads/<upload_id>
GET  /api/uploads/<upload_id>/thumbnail
POST /api/printers/<printer_id>/uploads/<upload_id>/send
POST /api/printers/<printer_id>/files/upload
```

Control:

```text
GET  /control
GET  /api/printers/<printer_id>/control/status
POST /api/printers/<printer_id>/control/fan
POST /api/printers/<printer_id>/control/temperature
POST /api/printers/<printer_id>/control/speed
POST /api/printers/<printer_id>/control/move
POST /api/printers/<printer_id>/control/home
POST /api/printers/<printer_id>/control/light
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

### Failure Detection does nothing while idle

That is expected. Current behavior is active-print-only monitoring. The loop still wakes lightly to check status so it can resume when a print starts, but it avoids heavy AI/vision work while idle.

### Control page buttons fail with error 1003

Error `1003` means the printer rejected the command parameters. cc2-dash now uses the stock portal payload shapes for Control commands, but firmware differences can still happen. Check:

1. Printer is online and not actively printing.
2. Printer Manager → Commands enabled.
3. Printer Manager → Dangerous commands enabled for jog/home only.
4. The stock portal can perform the same command.
5. **Logs → command** for the raw method/payload/response.

### Fan percentages look wrong

The CC2 stock portal shows fans as percentages, but the command payload uses 0–255 style values. cc2-dash converts both directions. If a fan still looks wrong, compare with the stock portal and check whether the firmware reports the fan under `fan`, `aux_fan`, `box_fan`, `ModelFan`, `AuxiliaryFan`, or `BoxFan`.

### Temperature inputs do not match the printer

The Control page prefers the firmware-reported target/set temperature. If target temperature is missing from telemetry, it may fall back to blank or current values depending on what the printer reports. Compare against the stock portal and check **Logs → command** after sending a new target.

### File Manager video download/export fails

For timelapse rows, use **Export** first. cc2-dash starts the export in the backend and shows **Time-lapse video generating…** while the printer creates the MP4. Download stays disabled until the printer Video List reports the video as generated/download-ready. The initial export command can return before the MP4 is done, so cc2-dash keeps showing generation status until the list confirms readiness.

If export eventually errors or times out, refresh Video List and compare with the stock Elegoo portal. The backend waits up to about 30 minutes for the printer to mark the video as generated, and the phone UI polls for up to about 35 minutes. The printer firmware may still finish generation after cc2-dash polling stops, but cc2-dash cannot force firmware-side timelapse creation if the stock portal also fails.

### Filament sensor says unknown

The app normalizes several known stock/raw sensor paths, but firmware may report different shapes depending on mode, CANVAS state, or printer firmware. Check **Logs → filament** and compare against the stock portal.

---

## Known limitations

- Private/hobby LAN use only; not production-hardened.
- Windows is untested.
- AI/vision monitoring is advisory only.
- AI does not automatically cancel jobs. Optional auto-pause is opt-in, countdown-gated, and pause-only.
- Vision checks can produce false positives and false negatives.
- Camera quality, lighting, glare, focus, and angle matter a lot.
- Firmware response shapes may vary by version.
- Timelapse/video export may not work even in the stock portal.
- File Manager and Filament Manager remain locked and firmware-sensitive. Control and Upload are enabled but still firmware-sensitive.
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
│   ├── print_state.py
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
│   ├── control.html
│   ├── feature_disabled.html
│   ├── filaments.html
│   ├── files.html
│   ├── index.html
│   ├── kiosk.html
│   ├── logs.html
│   ├── portal.html
│   ├── settings.html
│   ├── setup.html
│   └── upload.html
├── install.sh
├── run.sh
├── uninstall.sh
├── requirements.txt
└── README.md
```

---

## Release notes


### v1.2.63 timelapse export confirmation

- Fixed timelapse export jobs reporting complete too early on longer videos by treating the initial firmware export response as an acknowledgement only.
- Backend export jobs now keep polling the printer Video List until the selected row reports generated/download-ready before exposing the Download URL.
- Extended timelapse export waiting to better handle longer videos: backend timeout is about 30 minutes; mobile UI polling is about 35 minutes.
- Added a friendly mapping for sub-status `3020` so the dashboard shows **Time-lapse video generating** instead of `Sub 3020`.
- Updated File Manager docs and troubleshooting notes for confirmed export readiness.

### v1.2.62 async timelapse export

- Reworked File Manager → Video List timelapse export so the browser no longer waits on the long-running firmware export request.
- Added backend timelapse export jobs with polling status: generating, ready/complete, or error.
- Updated the Video List UI so status `1`/not-yet-generated rows require **Export** before **Download**; Download is disabled until the generated video is ready.
- Added visible **Time-lapse video generating…** status with row-level spinner/state so the phone browser can sit on a lightweight polling loop instead of timing out.
- Kept downloads proxied through cc2-dash after export so the stock printer `/download` URL/PIN stays behind the dashboard.
- Updated File Manager documentation and troubleshooting notes for the export-first workflow.

### v1.2.61 ROI feedback mobile/cache fix

- Adds cache-busting query strings to `/static/app.css` and `/static/app.js` so phone browsers pick up new dashboard JavaScript/CSS after upgrades.
- Themes the Report Missed Failure button with a fallback for browsers that do not support newer CSS color-mix rules.
- Prevents the Report Missed Failure button from also firing the normal generic feedback handler.
- Makes the ROI feedback modal opening handler delegated and mobile-safe so it still works if dashboard markup is refreshed later.

### v1.2.60 ROI missed-failure feedback

- Added a **Report Missed Failure** dashboard workflow for localized failures the detector missed, especially detached small parts, fallen prime towers, and air-printing zones.
- Added a mobile-safe ROI annotation modal using a frozen still frame plus SVG/pointer-event drawing, so mouse, touch, and stylus all use the same code path.
- Added `POST /api/printers/<printer_id>/ai/feedback/frame` to capture a still feedback frame before the user draws an ROI box.
- Extended `POST /api/printers/<printer_id>/ai/feedback` with optional ROI annotation metadata while keeping existing Looks Good / Looks Bad / False Alarm feedback compatible.
- Saved normalized ROI coordinates, tight ROI crops, padded context crops, and annotation metadata into JSONL/SQLite raw training data and AI Training review/export flows.
- Added safe image-serving endpoints for feedback frames and ROI crops. ROI evidence is stored for learning/review only in this release and does not alter auto-pause behavior.

### v1.2.59 failure-detection pause gate

- Added a shared print-state helper module so dashboard status, Portal AI, vision checks, and auto-pause permission use the same preparation/active-print/pause-safety classification instead of drifting apart across files.
- Added a single Failure Detection pause-permission gate that returns explicit allowed actions, veto reasons, evidence, failure family, and a hard `cancel_allowed: false` decision.
- Hardened auto-pause so a countdown only arms when the gate allows pause, and the backend performs a fresh status + forced vision recheck before sending `PAUSE_PRINT`.
- Hardened the dashboard **Pause now** action so it requires the active auto-pause token and also passes the same fresh recheck/gate before sending a pause command.
- Made camera/view-quality and telemetry-only warnings inspect/warn states rather than auto-pause triggers, keeping auto-pause reserved for pause-grade print failure evidence.

### v1.2.58 control, upload, error-code, and release-lock polish

- Updated the top cc2-dash banner link so it opens the public GitHub repository instead of linking back to the app host.
- Added printer exception-code decoding so known firmware exception IDs display with human-readable meanings, such as `1252 — Extruder unload failure / unload timeout`, instead of only raw bracketed codes.
- Reworked Control page command payloads to match the bundled stock Elegoo portal more closely: `{homed_axes}` for home, `{axes, distance}` for jog, `{power}` for light, stock fan keys, and stock speed modes.
- Fixed fan percentage handling by converting the UI's 0–100% values to the stock portal's 0–255 fan command scale, and converting telemetry back for display.
- Added Control page bed/extruder current/target temperature display plus set/off controls using method `1028` with `{extruder}` or `{heater_bed}`.
- Polished Control page refresh behavior so telemetry updates do not overlap or stomp fan/temperature inputs while the user is editing them.
- Re-enabled the community-release master lock for File Manager and Filament Manager while leaving Control enabled and protected by runtime safety gates.
- Documented the enabled Upload page workflow, staged G-code review, thumbnail/metadata extraction, and upload/upload-and-print endpoints.
- Updated README status, safety notes, API endpoint lists, troubleshooting, known limitations, and release-gate explanation to match the current code.


### v1.2.57 community release prep

- Locked the experimental File Manager and Filament Manager off for public test builds while keeping their source, templates, routes, and command code in place for later re-enabling. Control was restored in v1.2.58 with runtime safety gates.
- Added a single release-gate switch in `cc2_dash/config.py`: `COMMUNITY_RELEASE_EXPERIMENTAL_LOCKS`. Set it to `False` to restore normal Settings toggles and direct access for listed release-locked features when they are ready.
- Server-side config migration and save handling now force locked experimental feature flags to `false`, including when users edit raw JSON.
- Settings now shows locked experimental rows with disabled controls instead of presenting them as public-ready options.
- Direct experimental pages now show a clean disabled-feature notice, and command-heavy experimental APIs return a clear 403 while locked. Dashboard G-code thumbnail image support remains available.
- Polished README wording to make the public-test status, safety boundaries, and experimental feature status clearer.

### v1.2.56 offline state detection

- Adds backend connection-health classification separate from printer job state: online, stale, offline, connecting, and registration/auth error.
- Uses MQTT registration plus fresh heartbeat/PONG timing as the primary source of truth so stale cached telemetry no longer makes the dashboard keep saying Printing or Idle when the printer is disconnected.
- Status payloads now include `connection_state`, `offline`, `stale`, `connection_reason`, and `connection_health` fields.
- Dashboard, Kiosk, Control page, browser title, and Failure Detection now show Offline / Connection Stale / Connecting instead of stale job state text.
- Failure Detection and auto-pause are paused while printer telemetry is offline/stale, and the Control page locks all command controls until the printer is online again.

### v1.2.55 control camera relay fix

- Fixed the Control page camera panel so it explicitly uses the cc2-dash camera relay stream endpoint from Control status instead of relying only on the initial template image state.
- Added Control-page camera load/failure handlers that hide the loading overlay when the relay starts producing frames and retry the relay stream if the browser drops the MJPEG connection.
- Removed the extra descriptive blurbs from the Control page hero and camera card to keep the page tighter on mobile.

### v1.2.54 control page print lock, camera, and light toggle polish

- The Control page now locks all printer command controls while a print job is active, including movement, homing, speed, fans, and the Control-page light switch. The Refresh/status path remains available.
- Added a compact live camera relay panel at the top of the Control page so movement/control checks can be done without jumping back to the dashboard.
- Added backend active-print protection for Control page fan, speed, move, home, and Control-page light commands.
- Changed Dashboard → Quick Actions → Light from a push button into a themed pill toggle with an on/off icon and live state sync from printer telemetry.
- Exposed `light_on` in dashboard status payloads so the dashboard and Control page can share the same light state.

### v1.2.53 stock-style control page

- Added a new stock-Elegoo-inspired Control page with XY/Z jog controls, homing buttons, step-size buttons, print-speed presets, fan controls for Model / Assistance / Case, and a light toggle.
- Added `/control` plus `/api/printers/{printer_id}/control/*` endpoints for status, fan, speed, move, and home actions.
- Added a configurable Control top-nav item in Settings → Menu / Features. It is hidden by default, like the experimental Files/Filament pages.
- Reused the existing command safety gates: fan/speed/light require Commands enabled; jog/home require both Commands enabled and Dangerous commands enabled.
- Extended CC2 state normalization for stock position and fan-speed fields, including `CurrentFanSpeed.ModelFan`, `AuxiliaryFan`, and `BoxFan`.

### v1.2.52 themed toggle cleanup

- Replaced remaining native checkbox styling with the same cc2-dash themed pill switch style where it fits the UI, including generated dashboard/action/printer settings rows, setup wizard options, AI Training export options, and advanced JSON override.
- Themed checkbox-style controls now follow the active theme color instead of browser/default checkbox rendering.
- Removed the bulky recent feedback sample browser from Settings > Failure Detection because the AI Training page already owns sample review/export workflows.
- Settings still keep AI learning status, rebuild/import controls, and auto-pause configuration, but no longer duplicate the training sample list.

### v1.2.51 themed failure toggle fix

- Fixed the Failure Detection enable/disable switch so tapping it inside the collapsed dashboard card header reliably toggles detection instead of being eaten by the accordion summary click behavior.
- The same switch behavior is now shared by the dashboard card and Settings toggle, so enabling/disabling Failure Detection stays synchronized immediately.
- Restyled the dashboard and Settings switch controls with a fully custom themed pill toggle using the active cc2-dash theme color instead of browser/default checkbox styling.
- The Auto-pause on high-risk failure switch now uses the same themed control style.
- No changes to auto-pause safety behavior, pause command behavior, or failure scoring thresholds in this release.

### v1.2.50 failure detection auto-pause

- Renamed the dashboard `AI Info` card to `Failure Detection`.
- Added a modern pill-style enable/disable toggle on the Failure Detection card header, visible even while collapsed.
- Added live Failure Detection enable/disable behavior from the dashboard and Settings.
- Added opt-in automatic pause-on-failure behavior with configurable risk threshold, countdown seconds, cooldown minutes, and high-level-only requirement.
- Added a themed high-risk warning modal with red warning text, failure summary, countdown timer, Cancel Pause, Pause Now, and post-cancel feedback options.
- Backend watchdog now owns pending auto-pause countdowns so the pause can still fire even if the browser view is not the only thing ticking.
- Tuned failure scoring: telemetry rule disabling is honored more cleanly, startup temperature/filament grace windows reduce false alarms, and paused states no longer trigger progress-stall failures.
- Auto-pause remains conservative: it only considers active, connected, non-prep, non-paused print states. Cancel print is still not automated.

### v1.2.49 dashboard title and idle AI summary

- Updated the collapsed Failure Detection summary pill so idle/standby telemetry shows `Idle` instead of the generic `Looks Good` label.
- Added live browser tab title updates on the dashboard using the current printer status, progress percentage, and time remaining when available.
- Connection trouble now also reflects in the page title so a stale/offline tab is easier to spot.
- No changes to Portal AI scoring or print-prep guards in that release.

### v1.2.48 collapsed status summary

- Updated the collapsed Print Status card to show the printer telemetry status label, such as Bed Preheating, Extruder Preheating, Homing, Printing, or Idle, instead of always showing PRINTING during active/prep states.
- The collapsed summary now keeps the progress bar visible for active print-preparation phases and uses warning-colored styling for preparation states, green for real printing, muted for idle, and red for error/offline states.
- No changes to Portal AI scoring or print-prep guards in that release.

### v1.2.47 print-prep AI guard

- Added explicit print-preparation state detection for bed preheating, extruder preheating, homing, auto-leveling, self-checking, and initializing.
- Portal AI now reports these phases as Preparing instead of Failure Likely, and the dashboard summary pill shows Preparing instead of a scary false alarm.
- Vision/Ollama failure checks are paused during normal start-of-job preparation, preventing dark/empty-bed camera frames from causing false alarms.
- Temperature-gap, filament-out, and progress-stall rules are also paused during preparation and resume when actual printing starts.
- Added `print_phase`, `status_code`, and `sub_status_code` fields to status payloads for better debugging and future UI work.
- No printer-control/autopause behavior changed in that release; auto-pause arrives later in v1.2.50.

### v1.2.46 configurable navigation

- Added Menu / Features toggles for every top navigation item except Dash and Settings.
- Portal, Files, Filament, Kiosk, AI Training, and Logs can now be shown or hidden from Settings without disabling their underlying routes.
- Dash and Settings remain pinned so the dashboard and configuration page cannot be hidden accidentally.
- Kiosk top-nav Portal/Logs links now respect the same navigation visibility settings.
- Footer Console link now respects the Logs visibility setting.
- Updated config defaults/migration to preserve existing File Manager and Filament Manager hidden-by-default behavior.
- No AI scoring, learning, printer command, or safety behavior changes.

### v1.2.45 AI Training review page

- Added `/ai-training`, a lightweight review/export page for Portal AI feedback samples.
- Added sample review tools for relabeling feedback label, interpreted outcome, and reason/note.
- Added SQLite sample deletion from the review set while keeping JSONL audit rows and frame files.
- Added filtered dataset ZIP export with public metadata, raw JSONL rows, and optional captured frames.
- Added navigation link for AI Training.
- No changes to printer commands or AI advisory-only safety behavior.


### v1.2.44 JSONL feedback import

- Added explicit JSONL feedback import/backfill from `data/ai_feedback.jsonl` into the SQLite learning database.
- Added `POST /api/ai/learning/import-jsonl` with duplicate skipping, malformed-line counts, reason-update replay, and optional profile rebuild.
- Added Settings → Portal AI → Import old JSONL feedback controls with import summary output.
- Import is manual/on-demand only and does not run on every startup.
- Keeps JSONL as the human-readable audit log and stores images on disk only.
- No changes to printer commands, advisory-only AI safety behavior, or automatic print control.

### v1.2.43 dashboard learning badge

- Adds a compact AI learning badge/details panel to the dashboard Portal AI card.
- Shows whether learning is off, suggesting, or auto-adjusting safely.
- Surfaces sample count, confidence, and manual/suggested/applied/effective thresholds when threshold data is available from the live vision path.
- Clarifies whether learned modifiers are being applied live or only suggested.
- Changes the feedback custom “Save note” button to a theme-safe success/green style instead of danger red.
- Keeps manual thresholds unchanged and Portal AI advisory-only.

### v1.2.42 feedback UI polish

- Fixed optional Portal AI feedback training reason controls so they remain readable across themes instead of appearing as white-on-white buttons.
- Reason chips, skip, and custom note save controls now use a dark red danger-style treatment with white text.
- Printer Manager in Settings now loads collapsed by default, matching the other Settings panels.
- No AI scoring, learning, printer command, or safety behavior changes.

### v1.2.41 recent feedback samples

- Added a lightweight Recent AI Feedback Samples review panel under Settings → Portal AI.
- Added filters for printer, interpreted outcome, feedback label, and sample count.
- Shows timestamp, printer, label, optional reason/note, outcome, file/stage/progress, risk/severity/confidence, local heuristic metrics, triggered flags, and captured feedback frame thumbnails/links when available.
- Added global `GET /api/ai/learning/samples` with pagination/filter query parameters.
- Enhanced `GET /api/printers/<printer_id>/ai/learning/samples` with the same lightweight public sample shape and filters.
- Added `GET /api/ai/learning/samples/<sample_id>/frame` to safely serve captured feedback frames from `data/ai_feedback_frames/`.
- Does not store image blobs in SQLite and does not change live AI scoring or printer-control behavior.

### v1.2.40 feedback reason chips

- Added optional reason chips after fast Portal AI feedback clicks.
- Feedback buttons still save immediately; reason selection is a second, optional training-quality step.
- Looks Bad reasons include spaghetti/stringing, detached print, blob/nozzle buildup, first-layer issue, layer shift, filament issue, camera bad/unclear, and custom notes.
- False Alarm reasons include normal supports, purge tower, infill pattern, reflection/glare, low light but visible, multicolor purge mess, camera angle, and custom notes.
- Looks Good reasons include normal print, normal idle, normal purge/supports, and custom notes.
- Reason updates are appended to `data/ai_feedback.jsonl` and attached to the matching SQLite feedback sample when available.
- This improves future learning/review data without changing live AI scoring, thresholds, or printer-control safety behavior.

### v1.2.39 active-file layer total lookup

- Improved dashboard layer progress when the live printer status only reports the current layer.
- cc2-dash now mirrors the stock Elegoo portal behavior more closely by looking up the active G-code file metadata for total layers when live telemetry omits it.
- Layer progress now prefers `current/total` from file metadata, falling back to `current/?` only when the file lookup cannot find a total layer count.
- File metadata lookups are cached so the dashboard poll loop does not hammer the printer.
- No printer-control, Portal AI, or learning behavior changes.

### v1.2.38 layer progress unknown-total polish

- Updated dashboard layer display so printers that report current layer but not total layers now show `current/?` instead of only `current`.
- Added `layer_total_missing` to print metrics/status payloads so the UI/API can clearly distinguish "known current layer, unknown total layer" from a simple single-value layer display.
- No live AI, printer command, or learning behavior changes in this patch.

### v1.2.37 dashboard/settings polish

- Settings panels now load collapsed by default so the Portal AI / learning controls are not buried under a forever-scroll wall.
- Removed the unreliable **Filament Used** field from the dashboard Print Status card.
- Updated the dashboard Layer display to prefer current/total layer values such as `18/250`.
- Removed the small **API Reachable** status field from the dashboard Print Status card to reduce noise.
- Live AI learning behavior is unchanged from v1.2.36.

### v1.2.36 safe auto thresholds

- Wired persistent AI learning effective thresholds into the live vision monitor.
- In `off` and `suggest_only` modes, live vision continues to use the manual threshold values exactly as before.
- In `auto_adjust_safe` mode, live vision uses bounded effective values for dark luma, fine-edge density, and required bad checks.
- Manual settings are still never overwritten; learned values are applied only to the in-memory vision check configuration.
- Vision API results now include `learning_thresholds` and `learning_applied` so the dashboard/logs can explain when bounded modifiers were used.
- Added dashboard Vision metadata showing learning mode and applied modifiers during live checks.
- Added low-noise warning fallback: if the learning database/config lookup fails, vision monitoring falls back to manual thresholds instead of failing the check.
- Failure Detection auto-pause is now available in v1.2.50 as an opt-in pause-only guard; cancel/resume/control actions remain manual.

### v1.2.35 learning settings UI

- Added a Settings → Portal AI → AI Feedback Learning section.
- Added controls for persistent learning enablement, learning mode, minimum sample counts, maximum learned adjustment bounds, modifier-type toggles, and rebuild-on-feedback behavior.
- Added per-printer learning profile cards showing sample counts, true/false positive/negative outcomes, manual/suggested/applied/effective thresholds, normal baselines, confidence, and explanation reasons.
- Added Settings buttons to refresh learning status, rebuild all profiles, and reset learned tuning while keeping feedback samples and JSONL audit logs.
- Kept live AI scoring unchanged in that version; suggest-only mode remains the default. Auto-pause arrives later in v1.2.50 as an opt-in pause-only guard.

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

- Added compact Portal AI status pill to the collapsed AI/Failure Detection header.

### v1.2.17 collapsed progress

- Added compact progress bar to collapsed Print Status header.

### v1.2.16 dashboard section split

- Split Camera, Print Status, AI/Failure Detection, Quick Actions, and Connection into clearer collapsible sections.

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
