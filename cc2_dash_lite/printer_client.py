from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .logger import log


def _num(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except Exception:
        return default


class PrinterClient:
    def __init__(self, printer_id: str, printer: dict, cfg: dict):
        self.printer_id = printer_id
        self.printer = printer
        self.cfg = cfg
        self.host = printer.get("host") or printer.get("ip")
        self.timeout = float(cfg.get("advanced", {}).get("request_timeout_seconds", 2.5))

    @property
    def base_url(self) -> str:
        return self.printer.get("api_base_url") or f"http://{self.host}"

    async def _tcp_reachable(self, port: int = 80, timeout: float = 0.5) -> bool:
        if not self.host:
            return False
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, port), timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def get_status(self) -> dict:
        started = time.time()
        if not self.host:
            return self._empty_status("No host configured", reachable=False)

        status_paths = self.printer.get("status_paths") or self.cfg.get("advanced", {}).get("status_paths", [])
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for path in status_paths:
                url = self.base_url.rstrip("/") + "/" + str(path).lstrip("/")
                try:
                    resp = await client.get(url)
                    if resp.status_code >= 400:
                        continue
                    data = resp.json()
                    normalized = self._normalize_status(data)
                    normalized["raw_source"] = url
                    normalized["latency_ms"] = round((time.time() - started) * 1000)
                    return normalized
                except Exception:
                    continue

        reachable = await self._tcp_reachable(80) or await self._tcp_reachable(8080)
        if reachable:
            return self._empty_status("Reachable, but no known status endpoint answered", reachable=True)
        return self._empty_status("Printer not reachable", reachable=False)

    def _empty_status(self, message: str, reachable: bool) -> dict:
        state = "standby" if reachable else "offline"
        return {
            "printer_id": self.printer_id,
            "name": self.printer.get("name", self.printer_id),
            "host": self.host,
            "reachable": reachable,
            "state": state,
            "status_text": "Standing By" if reachable else "Offline",
            "message": message,
            "progress": 0,
            "print_time": "-",
            "time_left": "-",
            "completion": "-",
            "filament_used": "-",
            "hotend_current": None,
            "hotend_target": None,
            "bed_current": None,
            "bed_target": None,
            "file": "-",
            "updated_at": int(time.time()),
            "camera_url": self.printer.get("camera_url") or f"http://{self.host}:8080/",
            "portal_url": self.printer.get("portal_url") or f"http://{self.host}/",
        }

    def _normalize_status(self, data: dict) -> dict:
        # This normalizer intentionally accepts many common names. The real CC2 adapter can be
        # tightened later once the exact local API fields are locked down.
        d = data.get("data", data) if isinstance(data, dict) else {}
        state = d.get("state") or d.get("status") or d.get("printStatus") or d.get("printer_state") or "unknown"
        progress = _num(d.get("progress") or d.get("percent") or d.get("completion") or d.get("print_progress"), 0) or 0
        if progress > 1 and progress <= 100:
            progress_pct = progress
        elif progress <= 1:
            progress_pct = progress * 100
        else:
            progress_pct = 0
        hotend = d.get("hotend") or d.get("nozzle") or d.get("extruder") or {}
        bed = d.get("bed") or d.get("heater_bed") or {}
        return {
            "printer_id": self.printer_id,
            "name": self.printer.get("name", self.printer_id),
            "host": self.host,
            "reachable": True,
            "state": str(state).lower(),
            "status_text": str(state).replace("_", " ").title(),
            "message": "Status updated",
            "progress": round(progress_pct, 1),
            "print_time": d.get("print_time") or d.get("printTime") or d.get("elapsed") or "-",
            "time_left": d.get("time_left") or d.get("remaining") or d.get("timeRemaining") or "-",
            "completion": f"{round(progress_pct, 1)}%",
            "filament_used": d.get("filament_used") or d.get("filamentUsed") or "-",
            "hotend_current": _num(hotend.get("current") if isinstance(hotend, dict) else d.get("hotend_current")),
            "hotend_target": _num(hotend.get("target") if isinstance(hotend, dict) else d.get("hotend_target")),
            "bed_current": _num(bed.get("current") if isinstance(bed, dict) else d.get("bed_current")),
            "bed_target": _num(bed.get("target") if isinstance(bed, dict) else d.get("bed_target")),
            "file": d.get("file") or d.get("filename") or d.get("gcode_file") or "-",
            "updated_at": int(time.time()),
            "camera_url": self.printer.get("camera_url") or f"http://{self.host}:8080/",
            "portal_url": self.printer.get("portal_url") or f"http://{self.host}/",
            "raw": data,
        }

    async def run_action(self, action_id: str) -> dict:
        endpoints = self.printer.get("command_endpoints") or self.cfg.get("advanced", {}).get("command_endpoints", {})
        action_spec = endpoints.get(action_id)
        if not action_spec:
            msg = f"No command endpoint configured for {action_id}. Open the Elegoo portal for this action or add an endpoint in Advanced settings."
            log("warn", msg, "command", printer=self.printer_id, action=action_id)
            return {"ok": False, "status": 501, "message": msg}

        specs = action_spec if isinstance(action_spec, list) else [action_spec]
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            last_error = None
            for spec in specs:
                method = str(spec.get("method", "POST")).upper()
                path = str(spec.get("path", "/"))
                body = spec.get("json")
                url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
                try:
                    resp = await client.request(method, url, json=body)
                    if resp.status_code < 400:
                        log("info", f"Command {action_id} sent", "command", printer=self.printer_id)
                        return {"ok": True, "status": resp.status_code, "message": f"{action_id} command sent"}
                    last_error = f"HTTP {resp.status_code} from {url}"
                except Exception as exc:
                    last_error = str(exc)
        msg = f"Command {action_id} failed: {last_error or 'unknown error'}"
        log("error", msg, "command", printer=self.printer_id)
        return {"ok": False, "status": 500, "message": msg}
