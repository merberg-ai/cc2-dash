from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cc2_dash.cc2.commands import fan_percent_to_pwm, method_allowed, temperature_params
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


@app.get("/api/printers/{printer_id}/status")
def printer_status(printer_id: str) -> dict:
    client = manager.ensure_client(printer_id)
    return client.snapshot()


class CommandBody(BaseModel):
    method: int
    params: dict = {}
    wait: bool = True
    timeout: float = 10.0


@app.post("/api/printers/{printer_id}/command")
def command(printer_id: str, body: CommandBody) -> dict:
    printer = config_store.get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    if not method_allowed(body.method, printer.allow_commands, printer.allow_dangerous_commands):
        raise HTTPException(status_code=403, detail="Method blocked by safety policy")
    client = manager.ensure_client(printer_id)
    return client.send_command(method=body.method, params=body.params, wait=body.wait, timeout=body.timeout)


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
        return

    return StreamingResponse(iterator(), headers={"Cache-Control": "no-store, no-cache"})
