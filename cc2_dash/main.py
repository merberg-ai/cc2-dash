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
app = FastAPI(title="cc2-dash")
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
    return {"ok": True}


@app.get("/api/discover")
def api_discover(timeout: float = 5.0, target: str = "255.255.255.255") -> dict:
    printers = discover(timeout=timeout, target=target)
    return {"count": len(printers), "printers": printers}


@app.get("/api/printers")
def list_printers() -> list[dict]:
    return [p.model_dump() for p in config_store.list_printers()]


@app.post("/api/printers")
def upsert_printer(printer: PrinterConfig) -> dict:
    saved = config_store.upsert(printer)
    if saved.enabled:
        manager.restart(saved.id)
    return saved.model_dump()


@app.patch("/api/printers/{printer_id}")
def patch_printer(printer_id: str, patch: dict) -> dict:
    current = config_store.get(printer_id)
    if not current:
        raise HTTPException(status_code=404, detail="Printer not found")
    merged = PrinterConfig(**{**current.model_dump(), **patch, "id": printer_id})
    saved = config_store.upsert(merged)
    if saved.enabled:
        manager.restart(saved.id)
    return saved.model_dump()


@app.delete("/api/printers/{printer_id}")
def delete_printer(printer_id: str) -> dict:
    manager.stop(printer_id)
    return {"deleted": config_store.delete(printer_id)}


@app.post("/api/printers/{printer_id}/restart")
def restart(printer_id: str) -> dict:
    manager.restart(printer_id)
    return {"ok": True}


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


@app.post("/api/printers/{printer_id}/command")
def command(printer_id: str, body: CommandBody) -> dict:
    return _send(printer_id, body.method, body.params, body.wait, body.timeout)


@app.post("/api/printers/{printer_id}/light")
def light(printer_id: str, body: dict) -> dict:
    return _send(printer_id, 1029, light_params(bool(body.get("on", True))))


@app.post("/api/printers/{printer_id}/temperature")
def temp(printer_id: str, body: dict) -> dict:
    return _send(printer_id, 1028, temperature_params(body.get("nozzle"), body.get("bed")))


@app.post("/api/printers/{printer_id}/fans")
def fans(printer_id: str, body: dict) -> dict:
    return _send(printer_id, 1030, fan_params(body.get("fan"), body.get("box_fan"), body.get("aux_fan")))


@app.post("/api/printers/{printer_id}/print/pause")
def pause(printer_id: str) -> dict:
    return _send(printer_id, 1021, {})


@app.post("/api/printers/{printer_id}/print/resume")
def resume(printer_id: str) -> dict:
    return _send(printer_id, 1023, {})


@app.post("/api/printers/{printer_id}/print/cancel")
def cancel(printer_id: str) -> dict:
    return _send(printer_id, 1022, {})


@app.post("/api/printers/{printer_id}/files/start")
def files_start(printer_id: str, body: dict) -> dict:
    return _send(printer_id, 1020, start_print_params(body["filename"], body.get("storage_media", "local")))


@app.get("/api/printers/{printer_id}/camera/url")
def camera_url(printer_id: str) -> dict:
    printer = config_store.get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    return {
        "url": f"http://{printer.host}:8080/",
        "direct_url": f"http://{printer.host}:8080/",
        "alt_direct_url": f"http://{printer.host}:8080/?action=stream",
        "proxy_url": f"/api/printers/{printer_id}/camera/stream",
    }


@app.post("/api/printers/{printer_id}/camera/enable")
def camera_enable(printer_id: str, body: dict | None = None) -> dict:
    body = body or {}
    return _send(printer_id, 1042, camera_enable_params(body.get("enable", True)), wait=False)


@app.get("/api/printers/{printer_id}/camera/stream")
async def camera_stream(printer_id: str):
    printer = config_store.get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")

    async def iterator():
        async with httpx.AsyncClient(timeout=None) as client:
            for url in (f"http://{printer.host}:8080/", f"http://{printer.host}:8080/?action=stream"):
                try:
                    async with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            continue
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                        return
                except Exception:
                    continue

    return StreamingResponse(iterator(), headers={"Cache-Control": "no-store, no-cache"})


@app.get("/oe-relay-static/elegoo-os-relay.js")
def oe_js() -> PlainTextResponse:
    return PlainTextResponse("console.log('relay shim')", media_type="application/javascript")


@app.get("/oe-relay-static/elegoo-os-relay.css")
def oe_css() -> PlainTextResponse:
    return PlainTextResponse("/* relay shim */", media_type="text/css")
