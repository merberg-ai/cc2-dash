from __future__ import annotations

import threading
from typing import Dict, Optional

from cc2_dash_lite.config import PrinterConfig, load_config, printer_dict_to_config
from cc2_dash_lite.cc2.client import Cc2Client
from cc2_dash_lite.logger import log


class LitePrinterRuntime:
    """Small in-process MQTT client manager for configured CC2 printers."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.clients: Dict[str, Cc2Client] = {}

    def _config_for(self, printer_id: str) -> Optional[PrinterConfig]:
        cfg = load_config()
        data = (cfg.get("printers") or {}).get(printer_id)
        if not data:
            return None
        return printer_dict_to_config(printer_id, data)

    def start_all(self) -> None:
        cfg = load_config()
        for printer_id, data in (cfg.get("printers") or {}).items():
            pcfg = printer_dict_to_config(printer_id, data)
            if pcfg.enabled and pcfg.host and pcfg.serial and pcfg.access_code:
                self.start(printer_id, pcfg)

    def stop_all(self) -> None:
        with self.lock:
            clients = list(self.clients.values())
            self.clients.clear()
        for client in clients:
            client.stop()

    def start(self, printer_id: str, cfg: Optional[PrinterConfig] = None) -> bool:
        cfg = cfg or self._config_for(printer_id)
        if not cfg:
            return False
        if not (cfg.host and cfg.serial and cfg.access_code):
            log("warn", f"Not starting {printer_id}: missing host/serial/PIN", "cc2")
            return False
        with self.lock:
            client = self.clients.get(printer_id)
            if client is None:
                client = Cc2Client(cfg)
                self.clients[printer_id] = client
            client.start()
        log("info", f"Started CC2 MQTT client for {cfg.name} at {cfg.host}", "cc2")
        return True

    def stop(self, printer_id: str) -> bool:
        with self.lock:
            client = self.clients.pop(printer_id, None)
        if client is None:
            return False
        client.stop()
        log("info", f"Stopped CC2 MQTT client for {printer_id}", "cc2")
        return True

    def restart(self, printer_id: str, cfg: Optional[PrinterConfig] = None) -> bool:
        self.stop(printer_id)
        return self.start(printer_id, cfg)

    def reload(self) -> None:
        cfg = load_config()
        wanted = {}
        for printer_id, data in (cfg.get("printers") or {}).items():
            pcfg = printer_dict_to_config(printer_id, data)
            if pcfg.enabled and pcfg.host and pcfg.serial and pcfg.access_code:
                wanted[printer_id] = pcfg
        with self.lock:
            active = set(self.clients.keys())
        for printer_id in active - set(wanted.keys()):
            self.stop(printer_id)
        for printer_id, pcfg in wanted.items():
            self.restart(printer_id, pcfg) if printer_id in active else self.start(printer_id, pcfg)

    def get_client(self, printer_id: str) -> Optional[Cc2Client]:
        with self.lock:
            return self.clients.get(printer_id)

    def snapshot(self, printer_id: str) -> Optional[dict]:
        client = self.get_client(printer_id)
        if client:
            return client.snapshot()
        pcfg = self._config_for(printer_id)
        if not pcfg:
            return None
        return {
            "id": pcfg.id,
            "name": pcfg.name,
            "host": pcfg.host,
            "serial": pcfg.serial,
            "connected": False,
            "registered": False,
            "registration_error": None,
            "last_error": "client not running",
            "allow_commands": pcfg.allow_commands,
            "allow_dangerous_commands": pcfg.allow_dangerous_commands,
            "normalized": {},
            "attributes": {},
            "raw_status": {},
        }

    def snapshots(self) -> list[dict]:
        cfg = load_config()
        out = []
        seen = set()
        with self.lock:
            items = list(self.clients.items())
        for printer_id, client in items:
            seen.add(printer_id)
            out.append(client.snapshot())
        for printer_id, data in (cfg.get("printers") or {}).items():
            if printer_id not in seen:
                snap = self.snapshot(printer_id)
                if snap:
                    out.append(snap)
        return out
