# cc2-dash

Local LAN dashboard for the Elegoo Centauri Carbon 2 (CC2) 3D printer.

This dashboard keeps the working CC2 LAN integration pieces and provides a full custom web dashboard:

- **UDP Discovery**: Fast scanning on the local network (UDP port 52700) to find your printer.
- **Printer Pairing**: Safe pairing using the printer access code/PIN.
- **MQTT Bridging**: Bi-directional MQTT message translation for real-time printer status cards.
- **Stock Elegoo Portal Bridge**: Hosts a local, patched version of the stock Elegoo/OctoEverywhere portal, complete with a custom browser shim (`cc2dash-shim.js`) to handle native IPC calls.
- **G-code & Job Management**: Endpoints to list, detail, start, and delete G-code files.
- **Timelapse & History**: Downloader and exporter for recorded timelapses and print tasks.
- **Robust Camera Streams**: Seamless fallback between direct printer MJPEG stream (`http://<printer-ip>:8080/`) and a backend camera proxy stream (`/api/printers/{id}/camera/stream`).
- **Local User Authentication**: Role-based access control (`guest`, `viewer`, `operator`, and `admin`) stored securely with PBKDF2-SHA256 password hashing.
- **Persistent Configuration**: Saved configuration states under `config/printers.json` (printer list) and `config/app.json` (app behavior and preferences).

---

## Getting Started

### Prerequisites
- Python 3.9 or higher

### Installation & Run

#### Linux & macOS
```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the app
./scripts/run.sh
```

#### Windows (PowerShell)
```powershell
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run the app
python -m cc2_dash
```

Once started, open the web panel in your browser:
```text
http://localhost:8088/
```

---

## Directory Structure

```text
cc2-dash/
├── cc2_dash/
│   ├── auth/             # User authentication and store.py (PBKDF2-SHA256)
│   ├── cc2/              # Core printer communication (client, discovery, manager, state)
│   ├── elegoo_web/       # Staged stock Elegoo portal SPA and cc2dash-shim.js
│   ├── web/              # Custom responsive Vanilla CSS/JS dashboard interface
│   ├── app_config.py     # App settings manager (app.json)
│   ├── config.py         # Printer list manager (printers.json)
│   └── main.py           # FastAPI server and endpoint routing
├── config/               # Ignored by git; stores local user and printer databases
├── scripts/              # Unix installer and service helper scripts
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation
```

---

## Architecture & Mechanics

### 1. MQTT-over-WebSocket Bridge
The stock Elegoo SPA bundle communicates using MQTT-over-WebSocket. However, the printer itself only hosts a raw TCP MQTT broker on port `1883`. `cc2-dash` implements a lightweight, low-overhead WebSocket-to-TCP bridge in `main.py` (`/ws/mqtt/{printer_id}`) that transparently routes and pipes binary MQTT frames between the browser client and the printer.

### 2. Browser IPC Shim (`cc2dash-shim.js`)
When hosted inside a browser rather than the Elegoo Slicer window, the stock Elegoo portal expects a native desktop environment and calls `window.nativeIpc`. `cc2-dash` injects `cc2dash-shim.js` to translate these desktop IPC commands into FastAPI requests, enabling the stock portal to function directly in any normal browser.

### 3. Camera Proxy Fallback
The CC2 printer exposes an MJPEG stream on port `8080`.
- The dashboard first attempts to load this stream **directly** from the printer's IP to reduce server CPU load.
- If direct browser access fails (due to network routing, browser security, or CORS), the dashboard automatically falls back to the **backend proxy** (`/api/printers/{id}/camera/stream`), which pipes the MJPEG stream through the python backend.

---

## User Roles & Permissions

On first boot, the dashboard launches in **First-Run Admin Setup** mode, prompting you to create the initial admin user. Subsequent accesses use role-based security:

| Role | Permissions |
| :--- | :--- |
| **guest** | Read-only access to selected status cards (customizable in settings). No printer controls or file management. |
| **viewer** | Read-only access to dashboard, camera, and G-code file list. |
| **operator** | View dashboard, manage temperatures, toggle fans, pause/resume/start prints. |
| **admin** | Full capabilities, including adding/removing printers, managing users, editing security limits, and deleting files. |

---

## LAN Safety Defaults

Since the dashboard executes physical machine operations, it ships with conservative default policies:
- Wildcard CORS is disabled by default (configurable to `same-origin` or specific allowed origins).
- Access codes and PINs are redacted and never sent to the UI.
- All controls default to **off** for new printers.
- Dangerous commands (cancel, delete, start print) require explicit operator/admin activation and optional confirmation prompts.
- Target nozzle and bed temperatures are clamped on both the frontend and backend against safety limits stored in `config/app.json`.
