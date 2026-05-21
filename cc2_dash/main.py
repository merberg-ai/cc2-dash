from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cc2_dash.cc2.commands import camera_enable_params, fan_params, light_params, method_allowed, start_print_params, temperature_params
from cc2_dash.cc2.discovery import discover
from cc2_dash.cc2.manager import PrinterManager
from cc2_dash.config import ConfigStore, PrinterConfig

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="cc2-dash-v1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(ROOT / "web" / "static")), name="static")
app.mount("/elegoo", StaticFiles(directory=str(ROOT / "elegoo_web")), name="elegoo")

config_store = ConfigStore()
manager = PrinterManager(config_store)

@app.on_event("startup")
def startup() -> None:
    manager.start_all()

@app.on_event("shutdown")
def shutdown() -> None:
    manager.stop_all()

@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")

@app.get("/portal")
def portal() -> HTMLResponse:
    return HTMLResponse('<iframe src="/portal-fullscreen" style="width:100vw;height:100vh;border:0"></iframe>')

@app.get("/portal-fullscreen")
def portal_fullscreen() -> HTMLResponse:
    printers = config_store.list_printers()
    if not printers:
        return HTMLResponse("No printer configured", status_code=400)
    p = printers[0]
    qs = f"id={p.id}&ip={p.host}&print_ip={p.host}&sn={p.serial}&access_code={p.access_code}&username=elegoo&lang=en-US#/index"
    return HTMLResponse(f'<iframe src="/elegoo/octo_portal.html?{qs}" style="width:100vw;height:100vh;border:0"></iframe>')

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "message": "healthy", "data": {"app": "cc2-dash-v1.0"}}

@app.get("/api/config")
def get_config() -> dict:
    return {"ok": True, "message": "config loaded", "data": config_store.get_config().model_dump()}

@app.post("/api/config/save")
def save_config(body: dict) -> dict:
    saved = config_store.save_config(body)
    return {"ok": True, "message": "config saved", "data": saved.model_dump()}

@app.get("/api/discover")
def api_discover(timeout: float = 5.0, target: str = "255.255.255.255") -> dict:
    printers = discover(timeout=timeout, target=target)
    return {"ok": True, "message": "discover complete", "data": {"count": len(printers), "printers": printers}}

@app.get("/api/scan")
def api_scan(timeout: float = 5.0, target: str = "255.255.255.255") -> dict:
    return api_discover(timeout, target)

@app.get("/api/printers")
def list_printers() -> list[dict]:
    return [p.model_dump() for p in config_store.list_printers()]

@app.post("/api/printers")
def upsert_printer(printer: PrinterConfig) -> dict:
    saved = config_store.upsert(printer)
    if saved.enabled:
        manager.restart(saved.id)
    return saved.model_dump()

@app.get("/api/printer/status")
def printer_status_single(printer_id: str | None = None) -> dict:
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    if not pid:
        return {"ok": False, "message": "No printer configured", "error": "no_printer", "data": None}
    client = manager.ensure_client(pid)
    return {"ok": True, "message": "status", "data": client.snapshot()}

@app.get("/api/printers/{printer_id}/status")
def printer_status(printer_id: str) -> dict:
    client = manager.ensure_client(printer_id)
    return client.snapshot()

class CommandBody(BaseModel):
    method: int
    params: dict = {}
    wait: bool = True
    timeout: float = 10.0

def _send(printer_id: str, method: int, params: dict, wait: bool = True, timeout: float = 10.0) -> dict:
    printer = config_store.get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    if not method_allowed(method, printer.allow_commands, printer.allow_dangerous_commands):
        raise HTTPException(status_code=403, detail="Method blocked by safety policy")
    client = manager.ensure_client(printer_id)
    return client.send_command(method=method, params=params, wait=wait, timeout=timeout)

@app.post("/api/printer/command")
def command_single(body: CommandBody, printer_id: str | None = None) -> dict:
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    if not pid:
        return {"ok": False, "message": "No printer configured", "error": "no_printer", "data": None}
    return {"ok": True, "message": "command sent", "data": _send(pid, body.method, body.params, body.wait, body.timeout)}

@app.post("/api/printers/{printer_id}/command")
def command(printer_id: str, body: CommandBody) -> dict:
    return _send(printer_id, body.method, body.params, body.wait, body.timeout)

@app.get("/api/printer/files")
def files(printer_id: str | None = None) -> dict:
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    return {"ok": True, "message": "files", "data": _send(pid, 1044, {})} if pid else {"ok": False, "message": "No printer configured", "error": "no_printer", "data": []}

@app.post("/api/printer/files/start")
def files_start_single(body: dict, printer_id: str | None = None) -> dict:
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    return {"ok": True, "message": "start sent", "data": _send(pid, 1020, start_print_params(body["filename"], body.get("storage_media", "local")))}

@app.post("/api/printer/files/delete")
def files_delete_single(body: dict, printer_id: str | None = None) -> dict:
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    return {"ok": True, "message": "delete sent", "data": _send(pid, 1047, {"storage_media": body.get("storage_media", "local"), "file_path": body["file_path"]})}

@app.get("/api/printer/timelapse")
def timelapse(printer_id: str | None = None) -> dict:
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    if not pid:
        return {"ok": False, "message": "No printer configured", "error": "no_printer", "data": []}
    return {"ok": True, "message": "timelapse list", "data": _send(pid, 1036, {})}

@app.get('/api/printer/timelapse/download/{item_id}')
def timelapse_download(item_id: str) -> dict:
    return {"ok": False, "message": "Timelapse download endpoint not discovered", "error": "not_supported", "data": {"id": item_id}}

@app.post('/api/printer/timelapse/delete')
def timelapse_delete(body: dict, printer_id: str | None = None) -> dict:
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    return {"ok": True, "message": "delete request sent", "data": _send(pid, 1038, {"list": body.get("list", [])})}

@app.get('/api/camera/proxy')
async def camera_proxy(printer_id: str | None = None):
    pid = printer_id or (config_store.list_printers()[0].id if config_store.list_printers() else None)
    if not pid:
        raise HTTPException(status_code=404, detail="No printer configured")
    return await camera_stream(pid)

@app.get('/api/console/recent')
def console_recent() -> dict:
    return {"ok": True, "message": "local console is browser-side", "data": []}

@app.get('/api/printers/{printer_id}/camera/url')
def camera_url(printer_id: str) -> dict:
    printer = config_store.get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail='Printer not found')
    base = printer.camera_url_override or f"http://{printer.host}:{printer.camera_port}/"
    alt = f"http://{printer.host}:{printer.camera_port}/?action=stream"
    return {"url": base, "direct_url": base, "alt_direct_url": alt, "proxy_url": f"/api/printers/{printer_id}/camera/stream"}

@app.get('/api/printers/{printer_id}/camera/stream')
async def camera_stream(printer_id: str):
    printer = config_store.get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail='Printer not found')
    urls = [printer.camera_url_override or f"http://{printer.host}:{printer.camera_port}/", f"http://{printer.host}:{printer.camera_port}/?action=stream"]
    async def iterator():
        async with httpx.AsyncClient(timeout=None) as client:
            for url in urls:
                try:
                    async with client.stream('GET', url) as resp:
                        if resp.status_code != 200:
                            continue
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                        return
                except Exception:
                    continue
    return StreamingResponse(iterator(), headers={"Cache-Control": "no-store, no-cache"})

@app.get('/oe-relay-static/elegoo-os-relay.js')
def oe_js() -> PlainTextResponse:
    return PlainTextResponse("console.log('relay shim')", media_type='application/javascript')

@app.get('/oe-relay-static/elegoo-os-relay.css')
def oe_css() -> PlainTextResponse:
    return PlainTextResponse("/* relay shim */", media_type='text/css')
