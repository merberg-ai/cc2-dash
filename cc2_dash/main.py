from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import requests
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cc2_dash import __version__
from cc2_dash.config import ConfigStore, PrinterConfig, public_printer_dict, safe_printer_id
from cc2_dash.app_config import AppConfigStore
from cc2_dash.auth.store import AuthStore, normalize_username
from cc2_dash.cc2.discovery import discover
from cc2_dash.cc2.manager import PrinterManager
from cc2_dash.cc2.commands import (
    DELETE_FILE,
    ENABLE_WEBCAM,
    GET_CANVAS_STATUS,
    GET_DISK_INFO,
    GET_FILE_DETAIL,
    GET_FILE_LIST,
    GET_FILE_THUMBNAIL,
    GET_HISTORY_TASK,
    GET_TIME_LAPSE_VIDEO_LIST,
    HISTORY_DELETE,
    PAUSE_PRINT,
    RESUME_PRINT,
    SET_FAN_SPEED,
    SET_LIGHT,
    SET_PRINT_SPEED,
    SET_TEMPERATURE,
    START_PRINT,
    STOP_PRINT,
    delete_file_params,
    fan_params,
    file_detail_params,
    file_list_params,
    file_thumbnail_params,
    history_delete_params,
    light_params,
    method_allowed,
    print_speed_params,
    start_print_params,
    temperature_params,
    timelapse_export_params,
    webcam_params,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
LOG = logging.getLogger("cc2_dash")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"

store = ConfigStore()
app_settings = AppConfigStore()
auth_store = AuthStore()
manager = PrinterManager(store)

app = FastAPI(title="cc2-dash", version=__version__)
app.add_middleware(
    SessionMiddleware,
    secret_key=app_settings.ensure_session_secret(),
    session_cookie="cc2_dash_session",
    same_site="lax",
    https_only=bool(app_settings.section("auth").get("secure_cookie", False)),
    max_age=int(app_settings.section("auth").get("session_timeout_minutes", 720)) * 60,
)
_server_settings = app_settings.section("server")
_cors_mode = str(_server_settings.get("cors_mode", "same-origin")).lower()
_allowed_origins = _server_settings.get("allowed_origins") or []
if _cors_mode in {"wildcard", "permissive", "dev"}:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
ELEGEEGO_WEB_DIR = BASE_DIR / "elegoo_web"
if ELEGEEGO_WEB_DIR.exists():
    app.mount("/elegoo", StaticFiles(directory=str(ELEGEEGO_WEB_DIR), html=True), name="elegoo")


class PrinterIn(BaseModel):
    id: Optional[str] = None
    name: str = Field(default="Centauri Carbon 2")
    host: str
    serial: str
    access_code: str = "123456"
    port: int = 1883
    enabled: bool = True
    allow_commands: bool = False
    allow_dangerous_commands: bool = False


class CommandIn(BaseModel):
    method: int
    params: Dict[str, Any] = Field(default_factory=dict)
    wait: bool = True
    timeout: float = 10.0


class LightIn(BaseModel):
    on: bool


class PrinterSettingsIn(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    serial: Optional[str] = None
    access_code: Optional[str] = None
    port: Optional[int] = None
    enabled: Optional[bool] = None
    allow_commands: Optional[bool] = None
    allow_dangerous_commands: Optional[bool] = None


class TemperatureIn(BaseModel):
    nozzle: Optional[int] = None
    bed: Optional[int] = None


class FanIn(BaseModel):
    model: Optional[int] = None
    box: Optional[int] = None
    aux: Optional[int] = None
    values_are_pwm: bool = False


class SpeedIn(BaseModel):
    mode: int


class StartPrintIn(BaseModel):
    filename: str
    storage_media: str = "local"
    start_layer: int = 0
    calibration: bool = False
    platform_type: int = 0
    timelapse: bool = False


class DeleteFileIn(BaseModel):
    file_path: str
    storage_media: str = "local"


class TimelapseExportIn(BaseModel):
    url: str


class HistoryDeleteIn(BaseModel):
    task_ids: list[str | int]


class LoginIn(BaseModel):
    username: str
    password: str


class SetupAdminIn(BaseModel):
    username: str = "admin"
    password: str
    display_name: str = ""


class UserCreateIn(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    enabled: bool = True
    display_name: str = ""


class UserPatchIn(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    enabled: Optional[bool] = None
    display_name: Optional[str] = None


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str


LOGIN_FAILURES: Dict[str, Dict[str, Any]] = {}


def _auth_section() -> Dict[str, Any]:
    return app_settings.section("auth")


def _auth_enabled() -> bool:
    return bool(_auth_section().get("enabled", True))


def _permissions_for_role(role: str) -> Dict[str, bool]:
    perms = app_settings.section("permissions")
    role_perms = perms.get(role) if isinstance(perms, dict) else {}
    return {k: bool(v) for k, v in (role_perms or {}).items()}


def _identity(request: Request) -> Dict[str, Any]:
    auth = _auth_section()
    if not _auth_enabled():
        return {
            "authenticated": True,
            "username": "auth-disabled",
            "display_name": "Auth disabled",
            "role": "admin",
            "permissions": _permissions_for_role("admin"),
            "setup_required": False,
        }
    setup_required = not auth_store.has_admin()
    if setup_required:
        return {
            "authenticated": False,
            "username": None,
            "display_name": "",
            "role": "setup_required",
            "permissions": {},
            "setup_required": True,
        }
    session_user = request.session.get("username")
    login_at = float(request.session.get("login_at") or 0)
    timeout = int(auth.get("session_timeout_minutes", 720)) * 60
    if session_user and login_at and time.time() - login_at > timeout:
        request.session.clear()
        session_user = None
    if session_user:
        record = auth_store.get(session_user)
        if record and record.enabled:
            return {
                "authenticated": True,
                "username": record.username,
                "display_name": record.display_name,
                "role": record.role,
                "permissions": _permissions_for_role(record.role),
                "setup_required": False,
            }
        request.session.clear()
    role = "guest"
    permissions = _permissions_for_role(role) if auth.get("allow_guest_dashboard", True) else {}
    return {
        "authenticated": False,
        "username": None,
        "display_name": "",
        "role": role,
        "permissions": permissions,
        "setup_required": False,
    }


def _has_permission(request: Request, permission: str) -> bool:
    return bool(_identity(request).get("permissions", {}).get(permission))


def _require_permission(request: Request, permission: str) -> Dict[str, Any]:
    ident = _identity(request)
    if ident.get("setup_required"):
        raise HTTPException(428, "First-run admin setup is required")
    if not ident.get("permissions", {}).get(permission):
        if not ident.get("authenticated"):
            raise HTTPException(401, f"Login required: {permission}")
        raise HTTPException(403, f"Permission denied: {permission}")
    return ident


def _safe_return_url(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _record_login_failure(username: str) -> None:
    username = (username or "").strip().lower() or "unknown"
    item = LOGIN_FAILURES.setdefault(username, {"count": 0, "locked_until": 0.0})
    item["count"] = int(item.get("count") or 0) + 1
    auth = _auth_section()
    if auth.get("lockout_enabled", True) and item["count"] >= int(auth.get("max_failed_attempts", 8)):
        item["locked_until"] = time.time() + int(auth.get("lockout_minutes", 10)) * 60


def _login_locked(username: str) -> float:
    item = LOGIN_FAILURES.get((username or "").strip().lower()) or {}
    locked_until = float(item.get("locked_until") or 0)
    return max(0.0, locked_until - time.time())


def _clear_login_failures(username: str) -> None:
    LOGIN_FAILURES.pop((username or "").strip().lower(), None)


def _settings_for_identity(request: Request) -> Dict[str, Any]:
    # Everyone receives redacted settings so the UI can render. Only admins can patch.
    data = app_settings.public_dict(include_secret=False)
    ident = _identity(request)
    if not ident.get("permissions", {}).get("edit_settings"):
        # Guests/viewers do not need server/CORS internals.
        data.pop("server", None)
    data["auth_status"] = ident
    return data


def _public_printer_for_identity(cfg: PrinterConfig, ident: Dict[str, Any]) -> Dict[str, Any]:
    data = public_printer_dict(cfg, include_secret=False)
    if ident.get("role") == "guest":
        guest = app_settings.section("guest_dashboard")
        if not guest.get("show_printer_name", True):
            data["name"] = "Printer"
        if not guest.get("show_printer_ip", False):
            data["host"] = ""
        if not guest.get("show_serial", False):
            data["serial"] = ""
        data["allow_commands"] = False
        data["allow_dangerous_commands"] = False
    return data


def _filter_snapshot_for_identity(snapshot: Dict[str, Any], ident: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(snapshot)
    perms = ident.get("permissions", {})
    if not perms.get("developer_console"):
        out.pop("full_status", None)
    if ident.get("role") == "guest":
        guest = app_settings.section("guest_dashboard")
        normalized = dict(out.get("normalized") or {})
        if guest.get("mask_file_names", False) and normalized.get("file"):
            normalized["file"] = "Active job"
        if not guest.get("show_current_job", True):
            normalized["file"] = None
            normalized["layers"] = {}
        if not guest.get("show_temperatures", True):
            normalized["temps"] = {}
        if not guest.get("show_progress", True):
            normalized["progress"] = 0
        if not guest.get("show_eta", True):
            normalized["time"] = {}
        out["normalized"] = normalized
        out["allow_commands"] = False
        out["allow_dangerous_commands"] = False
    return out


def _developer_mode_enabled() -> bool:
    return bool(app_settings.section("dashboard").get("developer_mode"))


def _public_printer(cfg: PrinterConfig) -> Dict[str, Any]:
    return public_printer_dict(cfg, include_secret=False)


def _clamp_int(value: Optional[int], low: int, high: int) -> Optional[int]:
    if value is None:
        return None
    return max(low, min(high, int(value)))


def _safety_settings() -> Dict[str, Any]:
    return app_settings.section("safety")


def _validated_temperature(body: TemperatureIn) -> TemperatureIn:
    safety = _safety_settings()
    return TemperatureIn(
        nozzle=_clamp_int(body.nozzle, 0, int(safety.get("max_nozzle_temp", 320))),
        bed=_clamp_int(body.bed, 0, int(safety.get("max_bed_temp", 120))),
    )


def _validated_fans(body: FanIn) -> FanIn:
    safety = _safety_settings()
    max_pct = int(safety.get("max_fan_percent", 100))
    if body.values_are_pwm:
        max_pwm = round(max_pct / 100 * 255)
        return FanIn(
            model=_clamp_int(body.model, 0, max_pwm),
            box=_clamp_int(body.box, 0, max_pwm),
            aux=_clamp_int(body.aux, 0, max_pwm),
            values_are_pwm=True,
        )
    return FanIn(
        model=_clamp_int(body.model, 0, max_pct),
        box=_clamp_int(body.box, 0, max_pct),
        aux=_clamp_int(body.aux, 0, max_pct),
        values_are_pwm=False,
    )


@app.on_event("startup")
async def startup() -> None:
    manager.start_all()


@app.on_event("shutdown")
async def shutdown() -> None:
    manager.stop_all()


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")



def _portal_target(printer: Optional[str] = None) -> Optional[PrinterConfig]:
    configured = store.list_printers()
    if printer:
        exact = store.get(printer)
        if exact:
            return exact
        lowered = printer.lower()
        for cfg in configured:
            if cfg.host == printer or cfg.name.lower() == lowered or cfg.serial.lower() == lowered:
                return cfg
    return configured[0] if configured else None


@app.get("/portal", response_class=HTMLResponse)
def elegoo_portal(request: Request, printer: Optional[str] = None) -> HTMLResponse:
    _require_permission(request, "stock_portal")
    # Local CC2 firmware does not appear to expose OctoEverywhere's /index path
    # directly. OctoEverywhere is serving its own remote-access wrapper at /index.
    # So this wrapper now tries the printer root first and gives quick diagnostic
    # links for the likely local HTTP endpoints.
    cfg = _portal_target(printer)
    if not cfg:
        html = """<!doctype html><html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Elegoo Portal - cc2-dash</title><style>body{margin:0;background:#0f172a;color:#e5e7eb;font-family:Inter,system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}.card{max-width:680px;padding:28px;border:1px solid rgba(148,163,184,.22);border-radius:22px;background:rgba(15,23,42,.72);box-shadow:0 24px 80px rgba(0,0,0,.35)}a{color:#67e8f9}</style></head>
<body><div class="card"><h1>No printer configured yet</h1><p>Scan/add your Centauri Carbon 2 first, then come back to the Elegoo Portal tab.</p><p><a href="/">Back to cc2-dash</a></p></div></body></html>"""
        return HTMLResponse(html)

    root_url = f"http://{cfg.host}/"
    index_url = f"http://{cfg.host}/index"
    proxy_url = f"/portal-proxy/{cfg.id}/"
    octo_url = f"/portal-octo?printer={cfg.id}"
    diag_url = f"/api/portal-probe?printer={cfg.id}"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Elegoo Printer Portal - cc2-dash</title>
  <style>
    html,body{{margin:0;height:100%;background:#111827;color:#e5e7eb;font-family:Inter,system-ui,sans-serif;}}
    .bar{{min-height:46px;display:flex;gap:12px;align-items:center;padding:0 14px;background:rgba(17,24,39,.92);border-bottom:1px solid rgba(148,163,184,.18);backdrop-filter:blur(12px);flex-wrap:wrap}}
    .bar strong{{font-size:14px;white-space:nowrap}} .bar span{{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    .bar a{{color:#93c5fd;text-decoration:none;font-size:13px;white-space:nowrap}}
    iframe{{display:block;width:100%;height:calc(100vh - 47px);border:0;background:#202124;}}
  </style>
</head>
<body>
  <div class="bar"><strong>Elegoo portal launcher</strong><span>{cfg.name} · {cfg.host}</span><a href="/">Back</a><a href="{octo_url}" target="_blank">Open live portal</a><a href="{root_url}" target="_blank">Printer root</a><a href="{diag_url}" target="_blank">Probe paths</a></div>
  <iframe src="{octo_url}" title="Elegoo live portal"></iframe>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/api/portal-url")
def portal_url(request: Request, printer: Optional[str] = None) -> Dict[str, Any]:
    _require_permission(request, "stock_portal")
    cfg = _portal_target(printer)
    if not cfg:
        raise HTTPException(404, "No printer configured")
    return {"printer": _public_printer(cfg), "url": f"http://{cfg.host}/", "index_url": f"http://{cfg.host}/index", "proxy_url": f"/portal-proxy/{cfg.id}/"}


@app.get("/api/portal-probe")
def portal_probe(request: Request, printer: Optional[str] = None) -> Dict[str, Any]:
    _require_permission(request, "stock_portal")
    cfg = _portal_target(printer)
    if not cfg:
        raise HTTPException(404, "No printer configured")
    candidates = ["/", "/index", "/index.html", "/home", "/home.html", "/web", "/ui", "/dashboard", "/api", "/camera", "/stream", "/webcam", ":8080/", ":8080/?action=stream"]
    out = []
    with httpx.Client(timeout=2.5, follow_redirects=False) as client:
        for path in candidates:
            if path.startswith(":"):
                url = f"http://{cfg.host}{path}"
            else:
                url = f"http://{cfg.host}{path}"
            try:
                r = client.get(url)
                ctype = r.headers.get("content-type", "")
                text = r.text[:160].replace("\n", " ").replace("\r", " ") if "text" in ctype or "html" in ctype or "json" in ctype else ""
                out.append({"url": url, "status": r.status_code, "content_type": ctype, "server": r.headers.get("server", ""), "location": r.headers.get("location", ""), "sample": text})
            except Exception as exc:
                out.append({"url": url, "error": str(exc)})
    return {"printer": _public_printer(cfg), "results": out}


@app.api_route("/portal-proxy/{printer_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def portal_proxy(printer_id: str, path: str, request: Request) -> StreamingResponse:
    _require_permission(request, "stock_portal")
    cfg = store.get(printer_id) or _portal_target(printer_id)
    if not cfg:
        raise HTTPException(404, "Printer not found")
    target = f"http://{cfg.host}/{path}"
    if request.url.query:
        target += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length", "connection", "accept-encoding"}}
    body = await request.body()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        try:
            r = await client.request(request.method, target, headers=headers, content=body)
        except Exception as exc:
            raise HTTPException(502, f"Printer proxy failed: {exc}")
    excluded = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}
    content = r.content
    ctype = r.headers.get("content-type", "")
    if "text/html" in ctype:
        try:
            html = content.decode(r.encoding or "utf-8", errors="replace")
            # Make relative URLs stay inside our proxy when possible.
            base = f"/portal-proxy/{cfg.id}/"
            inject = f'<base href="{base}">'
            html = html.replace("<head>", "<head>" + inject, 1)
            content = html.encode("utf-8")
            resp_headers["content-type"] = "text/html; charset=utf-8"
        except Exception:
            pass
    return StreamingResponse(iter([content]), status_code=r.status_code, headers=resp_headers, media_type=resp_headers.get("content-type"))



@app.get("/portal-octo", response_class=HTMLResponse)
def octoeverywhere_style_portal(request: Request, printer: Optional[str] = None) -> HTMLResponse:
    _require_permission(request, "stock_portal")
    """Load the OctoEverywhere-style Elegoo SPA, but point MQTT over WebSocket back at cc2-dash."""
    cfg = _portal_target(printer)
    if not cfg:
        return HTMLResponse("""<!doctype html><html><body style="background:#111827;color:#e5e7eb;font-family:system-ui;padding:32px">
<h1>No printer configured</h1><p>Add/scan your printer first.</p><p><a style="color:#93c5fd" href="/">Back to cc2-dash</a></p></body></html>""")
    # The Elegoo/OctoEverywhere SPA uses a client-side /index route. When hosted
    # from /elegoo/octo_portal.html, browser history mode lands on /octo_portal.html
    # and renders a blank shell. The patched HTML below forces hash routing, so we
    # launch it at #/index. The stock bundle also separately looks for print_ip.
    app_url = (
        f"/elegoo/octo_portal.html"
        f"?id={cfg.id}&ip={cfg.host}&print_ip={cfg.host}&sn={cfg.serial}"
        f"&access_code={cfg.access_code}&username=elegoo&lang=en-US#/index"
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Elegoo Live Portal - cc2-dash</title>
  <style>
    html,body{{margin:0;height:100%;background:#111827;color:#e5e7eb;font-family:Inter,system-ui,sans-serif;}}
    .bar{{min-height:46px;display:flex;gap:12px;align-items:center;padding:0 14px;background:rgba(17,24,39,.92);border-bottom:1px solid rgba(148,163,184,.18);backdrop-filter:blur(12px);flex-wrap:wrap}}
    .bar strong{{font-size:14px;white-space:nowrap}} .bar span{{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    .bar a{{color:#93c5fd;text-decoration:none;font-size:13px;white-space:nowrap}}
    iframe{{display:block;width:100%;height:calc(100vh - 47px);border:0;background:#202124;}}
  </style>
</head>
<body>
  <div class="bar"><strong>Elegoo live portal</strong><span>{cfg.name} · MQTT WS bridge · {cfg.host}:{cfg.port}</span><a href="/">Back</a><a href="{app_url}" target="_blank">Open raw app</a><a href="/api/portal-probe?printer={cfg.id}" target="_blank">Probe</a></div>
  <iframe src="{app_url}" title="Elegoo live portal"></iframe>
</body>
</html>"""
    return HTMLResponse(html)



@app.get("/portal-fullscreen", response_class=HTMLResponse)
def portal_fullscreen(request: Request, printer: Optional[str] = None) -> HTMLResponse:
    _require_permission(request, "stock_portal")
    """Phone-first pure Elegoo portal view. No cc2-dash chrome once configured."""
    cfg = _portal_target(printer)
    if not cfg:
        return HTMLResponse("""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CC2 setup</title></head><body style="margin:0;background:#05070b;color:#e5e7eb;font-family:system-ui;display:grid;place-items:center;min-height:100vh;padding:20px;text-align:center"><div><h1>No printer configured</h1><p>Run setup first.</p><p><a style="color:#7dd3fc" href="/">Open setup</a></p></div></body></html>""")
    app_url = (
        f"/elegoo/octo_portal.html"
        f"?id={cfg.id}&ip={cfg.host}&print_ip={cfg.host}&sn={cfg.serial}"
        f"&access_code={cfg.access_code}&username=elegoo&lang=en-US#/index"
    )
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<meta name="theme-color" content="#202124" />
<title>Elegoo Portal</title>
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#202124}}iframe{{display:block;width:100vw;height:100dvh;border:0;background:#202124}}</style>
</head><body><iframe src="{app_url}" title="Elegoo Portal"></iframe></body></html>""")


@app.get("/oe-relay-static/elegoo-os-relay.js")
def oe_relay_js() -> HTMLResponse:
    # OctoEverywhere injects this helper in their hosted portal. For the local
    # cc2-dash bridge we already patched the MQTT URL inside the SPA bundle, so a
    # harmless stub keeps the page from throwing missing-script noise.
    return HTMLResponse(
        "console.log('[cc2-dash] local oe relay stub loaded');",
        media_type="application/javascript",
    )


@app.get("/oe-relay-static/elegoo-os-relay.css")
def oe_relay_css() -> HTMLResponse:
    return HTMLResponse("/* cc2-dash local oe relay css stub */", media_type="text/css")


@app.websocket("/ws/mqtt/{printer_id}")
async def mqtt_websocket_bridge(websocket: WebSocket, printer_id: str) -> None:
    """Very small MQTT-over-WebSocket to raw TCP MQTT bridge.

    The OctoEverywhere Elegoo page uses mqtt.js in WebSocket mode. Locally the
    printer exposes MQTT on TCP/1883, not a browser WebSocket endpoint, so this
    bridge just shuttles MQTT frames between the browser and the printer.
    """
    cfg = store.get(printer_id) or _portal_target(printer_id)
    if not cfg:
        await websocket.close(code=1008)
        return
    if _auth_enabled() and auth_store.has_admin():
        session = websocket.scope.get("session") or {}
        username = session.get("username")
        record = auth_store.get(username) if username else None
        perms = _permissions_for_role(record.role) if record and record.enabled else {}
        if not perms.get("stock_portal"):
            await websocket.close(code=1008)
            return
    await websocket.accept(subprotocol=websocket.headers.get("sec-websocket-protocol"))
    reader = writer = None
    try:
        reader, writer = await asyncio.open_connection(cfg.host, cfg.port)

        async def ws_to_tcp() -> None:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is None:
                    text = msg.get("text")
                    if text is None:
                        continue
                    data = text.encode("utf-8")
                writer.write(data)
                await writer.drain()

        async def tcp_to_ws() -> None:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)

        done, pending = await asyncio.wait(
            {asyncio.create_task(ws_to_tcp()), asyncio.create_task(tcp_to_ws())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        LOG.warning("MQTT WS bridge failed for %s: %s", printer_id, exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "version": __version__, "printers": len(store.list_printers())}


@app.get("/api/auth/me")
def auth_me(request: Request) -> Dict[str, Any]:
    ident = _identity(request)
    return {"ok": True, **ident}


@app.get("/api/auth/setup-required")
def auth_setup_required() -> Dict[str, Any]:
    return {"setup_required": _auth_enabled() and not auth_store.has_admin()}


@app.post("/api/auth/setup")
def auth_setup(request: Request, body: SetupAdminIn) -> Dict[str, Any]:
    if not _auth_enabled():
        raise HTTPException(400, "Auth is disabled")
    if auth_store.has_admin():
        raise HTTPException(409, "Admin user already exists")
    try:
        record = auth_store.create_user(body.username, body.password, role="admin", enabled=True, display_name=body.display_name)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    request.session.clear()
    request.session["username"] = record.username
    request.session["login_at"] = time.time()
    return {"ok": True, "user": record.public(), "me": _identity(request)}


@app.post("/api/auth/login")
def auth_login(request: Request, body: LoginIn) -> Dict[str, Any]:
    if not _auth_enabled():
        raise HTTPException(400, "Auth is disabled")
    if not auth_store.has_admin():
        raise HTTPException(428, "First-run admin setup is required")
    locked = _login_locked(body.username)
    if locked > 0:
        raise HTTPException(429, f"Too many failed logins. Try again in {int(locked // 60) + 1} minute(s).")
    record = auth_store.verify_login(body.username, body.password)
    if not record:
        _record_login_failure(body.username)
        raise HTTPException(401, "Invalid username or password")
    _clear_login_failures(body.username)
    request.session.clear()
    request.session["username"] = record.username
    request.session["login_at"] = time.time()
    return {"ok": True, "me": _identity(request)}


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> Dict[str, Any]:
    request.session.clear()
    return {"ok": True, "me": _identity(request)}


@app.post("/api/auth/change-password")
def auth_change_password(request: Request, body: PasswordChangeIn) -> Dict[str, Any]:
    ident = _identity(request)
    if not ident.get("authenticated") or not ident.get("username"):
        raise HTTPException(401, "Login required")
    record = auth_store.verify_login(ident["username"], body.old_password)
    if not record:
        raise HTTPException(401, "Current password is incorrect")
    updated = auth_store.update_user(ident["username"], password=body.new_password)
    return {"ok": True, "user": updated.public()}


@app.get("/api/auth/users")
def auth_list_users(request: Request) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    return {"ok": True, "users": auth_store.list_users()}


@app.post("/api/auth/users")
def auth_create_user(request: Request, body: UserCreateIn) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    try:
        user = auth_store.create_user(body.username, body.password, role=body.role, enabled=body.enabled, display_name=body.display_name)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": user.public()}


@app.patch("/api/auth/users/{username}")
def auth_update_user(request: Request, username: str, body: UserPatchIn) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    try:
        target = normalize_username(username)
        current = auth_store.get(target)
        if not current:
            raise KeyError(target)
        if target == _identity(request).get("username") and body.enabled is False:
            raise ValueError("You cannot disable your own logged-in account")
        if current.enabled and current.role == "admin" and (body.enabled is False or (body.role is not None and body.role != "admin")):
            admin_count = sum(1 for u in auth_store.list_users() if u.get("enabled") and u.get("role") == "admin")
            if admin_count <= 1:
                raise ValueError("At least one enabled admin user is required")
        user = auth_store.update_user(target, password=body.password, role=body.role, enabled=body.enabled, display_name=body.display_name)
    except KeyError:
        raise HTTPException(404, "User not found")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": user.public()}


@app.delete("/api/auth/users/{username}")
def auth_delete_user(request: Request, username: str) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    target = normalize_username(username)
    if target == _identity(request).get("username"):
        raise HTTPException(400, "You cannot delete your own logged-in account")
    record = auth_store.get(target)
    if not record:
        raise HTTPException(404, "User not found")
    if record.enabled and record.role == "admin":
        admin_count = sum(1 for u in auth_store.list_users() if u.get("enabled") and u.get("role") == "admin")
        if admin_count <= 1:
            raise HTTPException(400, "At least one enabled admin user is required")
    auth_store.delete_user(target)
    return {"ok": True}


@app.get("/api/settings")
def get_settings(request: Request) -> Dict[str, Any]:
    return _settings_for_identity(request)


@app.patch("/api/settings")
def patch_settings(request: Request, patch: Dict[str, Any]) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    try:
        return {"ok": True, "settings": app_settings.patch(patch)}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/settings/reset")
def reset_settings(request: Request) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    return {"ok": True, "settings": app_settings.reset()}


@app.get("/api/settings/export")
def export_settings(request: Request) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    return app_settings.public_dict(include_secret=False)


@app.get("/api/discover")
def api_discover(
    request: Request,
    timeout: float = Query(4.0, ge=0.5, le=15.0),
    target: str = Query("255.255.255.255"),
) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    printers = discover(timeout=timeout, target=target)
    return {"count": len(printers), "printers": [p.to_dict() for p in printers]}


@app.get("/api/printers")
def list_printers(request: Request) -> Dict[str, Any]:
    ident = _require_permission(request, "view_dashboard")
    snapshots = [_filter_snapshot_for_identity(snap, ident) for snap in manager.snapshots()]
    return {
        "configured": [_public_printer_for_identity(p, ident) for p in store.list_printers()],
        "status": snapshots,
    }


@app.post("/api/printers")
def add_printer(request: Request, printer: PrinterIn) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    printer_id = printer.id or safe_printer_id(printer.name or printer.serial or printer.host)
    cfg = PrinterConfig(
        id=printer_id,
        name=printer.name,
        host=printer.host,
        serial=printer.serial,
        access_code=printer.access_code,
        port=printer.port,
        enabled=printer.enabled,
        allow_commands=printer.allow_commands,
        allow_dangerous_commands=printer.allow_dangerous_commands,
    )
    store.upsert(cfg)
    if cfg.enabled:
        manager.restart(cfg.id)
    else:
        manager.stop(cfg.id)
    return {"ok": True, "printer": _public_printer(cfg)}


@app.patch("/api/printers/{printer_id}")
def update_printer(request: Request, printer_id: str, patch: PrinterSettingsIn) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    cfg = store.get(printer_id)
    if not cfg:
        raise HTTPException(404, "Printer not found")
    data = cfg.__dict__.copy()
    for key, value in patch.dict(exclude_unset=True).items():
        if value is None:
            continue
        if key == "access_code" and value == "":
            continue
        data[key] = value
    updated = PrinterConfig(**data)
    store.upsert(updated)
    if updated.enabled:
        manager.restart(updated.id)
    else:
        manager.stop(updated.id)
    return {"ok": True, "printer": _public_printer(updated)}


@app.delete("/api/printers/{printer_id}")
def delete_printer(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    manager.stop(printer_id)
    deleted = store.delete(printer_id)
    if not deleted:
        raise HTTPException(404, "Printer not found")
    return {"ok": True}


@app.post("/api/printers/{printer_id}/restart")
def restart_printer(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "edit_settings")
    if not manager.restart(printer_id):
        raise HTTPException(404, "Printer not found")
    return {"ok": True}


@app.get("/api/printers/{printer_id}/status")
def printer_status(request: Request, printer_id: str) -> Dict[str, Any]:
    ident = _require_permission(request, "view_dashboard")
    client = manager.get_client(printer_id)
    if not client:
        cfg = store.get(printer_id)
        if not cfg:
            raise HTTPException(404, "Printer not found")
        return _filter_snapshot_for_identity({"id": cfg.id, "name": cfg.name, "connected": False, "registered": False, "last_error": "not running", "normalized": {}}, ident)
    return _filter_snapshot_for_identity(client.snapshot(), ident)


def _send_printer_command(printer_id: str, body: CommandIn) -> Dict[str, Any]:
    cfg = store.get(printer_id)
    if not cfg:
        raise HTTPException(404, "Printer not found")
    if not method_allowed(body.method, cfg.allow_commands, cfg.allow_dangerous_commands):
        raise HTTPException(
            403,
            "Command blocked by cc2-dash safety settings. Enable allow_commands or allow_dangerous_commands for this printer if you really mean it.",
        )
    client = manager.get_client(printer_id)
    if not client:
        raise HTTPException(409, "Printer client is not running")
    try:
        result = client.send_request(body.method, body.params, wait=body.wait, timeout=body.timeout)
        return {"ok": True, "result": result}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/printers/{printer_id}/command")
def command(request: Request, printer_id: str, body: CommandIn) -> Dict[str, Any]:
    _require_permission(request, "dangerous_commands")
    return _send_printer_command(printer_id, body)


@app.get("/api/printers/{printer_id}/files")
def files(
    request: Request,
    printer_id: str,
    path: str = "/",
    storage_media: str = "local",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    _require_permission(request, "view_files")
    return _send_printer_command(printer_id, CommandIn(method=GET_FILE_LIST, params=file_list_params(path, storage_media, page, page_size, offset, limit), timeout=15.0))


@app.get("/api/printers/{printer_id}/files/detail")
def file_detail(request: Request, printer_id: str, filename: str, storage_media: str = "local", directory: Optional[str] = None) -> Dict[str, Any]:
    _require_permission(request, "view_files")
    return _send_printer_command(printer_id, CommandIn(method=GET_FILE_DETAIL, params=file_detail_params(filename, storage_media, directory)))


@app.get("/api/printers/{printer_id}/files/thumbnail")
def file_thumbnail(request: Request, printer_id: str, filename: str, storage_media: str = "local") -> Dict[str, Any]:
    _require_permission(request, "view_files")
    return _send_printer_command(printer_id, CommandIn(method=GET_FILE_THUMBNAIL, params=file_thumbnail_params(filename, storage_media), timeout=15.0))


@app.post("/api/printers/{printer_id}/files/delete")
def file_delete(request: Request, printer_id: str, body: DeleteFileIn) -> Dict[str, Any]:
    _require_permission(request, "delete_files")
    return _send_printer_command(printer_id, CommandIn(method=DELETE_FILE, params=delete_file_params(body.file_path, body.storage_media), timeout=15.0))


@app.post("/api/printers/{printer_id}/files/start")
def file_start(request: Request, printer_id: str, body: StartPrintIn) -> Dict[str, Any]:
    _require_permission(request, "start_print")
    return _send_printer_command(printer_id, CommandIn(method=START_PRINT, params=start_print_params(body.filename, body.storage_media, body.start_layer, body.calibration, body.platform_type, body.timelapse), timeout=20.0))


@app.get("/api/printers/{printer_id}/disk")
def disk(request: Request, printer_id: str, storage_media: str = "local") -> Dict[str, Any]:
    _require_permission(request, "view_files")
    return _send_printer_command(printer_id, CommandIn(method=GET_DISK_INFO, params={"storage_media": storage_media}))


@app.get("/api/printers/{printer_id}/canvas")
def canvas(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "view_dashboard")
    return _send_printer_command(printer_id, CommandIn(method=GET_CANVAS_STATUS, params={}))


@app.post("/api/printers/{printer_id}/light")
def light(request: Request, printer_id: str, body: LightIn) -> Dict[str, Any]:
    _require_permission(request, "control_print")
    return _send_printer_command(printer_id, CommandIn(method=SET_LIGHT, params=light_params(body.on)))


@app.post("/api/printers/{printer_id}/print/pause")
def print_pause(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "control_print")
    return _send_printer_command(printer_id, CommandIn(method=PAUSE_PRINT, params={}, timeout=60.0))


@app.post("/api/printers/{printer_id}/print/resume")
def print_resume(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "control_print")
    return _send_printer_command(printer_id, CommandIn(method=RESUME_PRINT, params={}, timeout=60.0))


@app.post("/api/printers/{printer_id}/print/cancel")
def print_cancel(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "dangerous_commands")
    return _send_printer_command(printer_id, CommandIn(method=STOP_PRINT, params={}, timeout=60.0))


@app.post("/api/printers/{printer_id}/temperature")
def set_temperature(request: Request, printer_id: str, body: TemperatureIn) -> Dict[str, Any]:
    _require_permission(request, "set_temperatures")
    body = _validated_temperature(body)
    params = temperature_params(body.nozzle, body.bed)
    if not params:
        raise HTTPException(400, "Provide nozzle and/or bed target")
    return _send_printer_command(printer_id, CommandIn(method=SET_TEMPERATURE, params=params, timeout=20.0))


@app.post("/api/printers/{printer_id}/fans")
def set_fans(request: Request, printer_id: str, body: FanIn) -> Dict[str, Any]:
    _require_permission(request, "set_fans")
    body = _validated_fans(body)
    params = fan_params(body.model, body.box, body.aux, body.values_are_pwm)
    if not params:
        raise HTTPException(400, "Provide at least one fan value")
    return _send_printer_command(printer_id, CommandIn(method=SET_FAN_SPEED, params=params, timeout=20.0))


@app.post("/api/printers/{printer_id}/speed")
def set_speed(request: Request, printer_id: str, body: SpeedIn) -> Dict[str, Any]:
    _require_permission(request, "control_print")
    return _send_printer_command(printer_id, CommandIn(method=SET_PRINT_SPEED, params=print_speed_params(body.mode), timeout=20.0))


@app.get("/api/printers/{printer_id}/history")
def history(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "view_timelapse")
    return _send_printer_command(printer_id, CommandIn(method=GET_HISTORY_TASK, params={}, timeout=20.0))


@app.post("/api/printers/{printer_id}/history/delete")
def history_delete(request: Request, printer_id: str, body: HistoryDeleteIn) -> Dict[str, Any]:
    _require_permission(request, "delete_files")
    return _send_printer_command(printer_id, CommandIn(method=HISTORY_DELETE, params=history_delete_params(body.task_ids), timeout=20.0))


@app.post("/api/printers/{printer_id}/timelapse/export")
def timelapse_export(request: Request, printer_id: str, body: TimelapseExportIn) -> Dict[str, Any]:
    _require_permission(request, "view_timelapse")
    return _send_printer_command(printer_id, CommandIn(method=GET_TIME_LAPSE_VIDEO_LIST, params=timelapse_export_params(body.url), timeout=180.0))


@app.post("/api/printers/{printer_id}/camera/enable")
def enable_camera(request: Request, printer_id: str) -> Dict[str, Any]:
    _require_permission(request, "control_print")
    return _send_printer_command(printer_id, CommandIn(method=ENABLE_WEBCAM, params=webcam_params(True), wait=False))


@app.get("/api/printers/{printer_id}/camera/url")
def camera_url(request: Request, printer_id: str) -> Dict[str, Any]:
    ident = _require_permission(request, "view_camera")
    cfg = store.get(printer_id)
    if not cfg:
        raise HTTPException(404, "Printer not found")
    direct_url = f"http://{cfg.host}:8080/"
    if ident.get("role") == "guest" and not app_settings.section("guest_dashboard").get("show_printer_ip", False):
        return {
            "url": f"/api/printers/{printer_id}/camera/stream",
            "direct_url": None,
            "alt_direct_url": None,
            "proxy_url": f"/api/printers/{printer_id}/camera/stream",
        }
    return {
        "url": direct_url,
        "direct_url": direct_url,
        "alt_direct_url": f"http://{cfg.host}:8080/?action=stream",
        "proxy_url": f"/api/printers/{printer_id}/camera/stream",
    }


@app.get("/api/printers/{printer_id}/camera/stream")
def camera_stream(request: Request, printer_id: str) -> StreamingResponse:
    _require_permission(request, "view_camera")
    cfg = store.get(printer_id)
    if not cfg:
        raise HTTPException(404, "Printer not found")

    # Ask printer to enable the camera stream if our MQTT client is connected. Ignore failures;
    # stock firmware often serves MJPEG on 8080 once the camera is awake anyway.
    client = manager.get_client(printer_id)
    if client and app_settings.section("camera").get("auto_wake", True) and _has_permission(request, "control_print"):
        try:
            client.send_request(ENABLE_WEBCAM, webcam_params(True), wait=False)
        except Exception as exc:
            LOG.debug("Camera enable command failed/ignored for %s: %s", printer_id, exc)

    # The CC2 camera presents an MJPEG stream on port 8080. Prefer root because
    # that is what direct browser access and the stock portal use. Some firmware
    # builds also answer /?action=stream, so keep it as a fallback.
    urls = [f"http://{cfg.host}:8080/", f"http://{cfg.host}:8080/?action=stream"]
    headers = {
        "User-Agent": "cc2-dash/" + __version__,
        "Accept": "multipart/x-mixed-replace,*/*",
        "Cache-Control": "no-cache",
    }

    upstream = None
    upstream_url = None
    last_error = None
    for url in urls:
        try:
            resp = requests.get(url, stream=True, timeout=(5, None), headers=headers)
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code} from {url}"
                LOG.warning("Camera proxy upstream returned HTTP %s for %s", resp.status_code, url)
                resp.close()
                continue
            upstream = resp
            upstream_url = url
            break
        except Exception as exc:
            last_error = str(exc)
            LOG.warning("Camera proxy stream failed for %s at %s: %s", printer_id, url, exc)

    if upstream is None:
        raise HTTPException(502, f"Camera stream unavailable: {last_error or 'no upstream response'}")

    content_type = upstream.headers.get("content-type") or "multipart/x-mixed-replace"
    LOG.info("Camera proxy connected to %s with content-type %s", upstream_url, content_type)

    def body_iter():
        try:
            for chunk in upstream.iter_content(chunk_size=16384):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        body_iter(),
        media_type=content_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/debug/config")
def debug_config(request: Request) -> Dict[str, Any]:
    _require_permission(request, "developer_console")
    if not _developer_mode_enabled():
        raise HTTPException(403, "Developer mode is disabled")
    return {
        "path": str(store.path),
        "app_config_path": str(app_settings.path),
        "printers": [_public_printer(p) for p in store.list_printers()],
        "settings": app_settings.public_dict(include_secret=False),
    }


def main() -> None:
    import uvicorn

    uvicorn.run("cc2_dash.main:app", host=str(app_settings.section("server").get("host", "0.0.0.0")), port=int(app_settings.section("server").get("port", 8088)), reload=False)


if __name__ == "__main__":
    main()
