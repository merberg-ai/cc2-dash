from __future__ import annotations

import threading
from typing import Dict, List, Optional

from cc2_dash.config import ConfigStore, PrinterConfig
from cc2_dash.cc2.client import Cc2Client


class PrinterManager:
    def __init__(self, store: ConfigStore) -> None:
        self.store = store
        self.lock = threading.RLock()
        self.clients: Dict[str, Cc2Client] = {}

    def start_all(self) -> None:
        for cfg in self.store.list_printers():
            if cfg.enabled:
                self.start(cfg.id)

    def stop_all(self) -> None:
        with self.lock:
            clients = list(self.clients.values())
        for client in clients:
            client.stop()

    def start(self, printer_id: str) -> bool:
        cfg = self.store.get(printer_id)
        if not cfg:
            return False
        with self.lock:
            client = self.clients.get(printer_id)
            if client is None:
                client = Cc2Client(cfg)
                self.clients[printer_id] = client
            client.start()
        return True

    def stop(self, printer_id: str) -> bool:
        with self.lock:
            client = self.clients.pop(printer_id, None)
        if client is None:
            return False
        client.stop()
        return True

    def restart(self, printer_id: str) -> bool:
        self.stop(printer_id)
        return self.start(printer_id)

    def reload_config(self) -> None:
        self.store.load()
        active = set(self.clients.keys())
        wanted = {p.id for p in self.store.list_printers() if p.enabled}
        for pid in active - wanted:
            self.stop(pid)
        for pid in wanted:
            self.restart(pid) if pid in active else self.start(pid)

    def get_client(self, printer_id: str) -> Optional[Cc2Client]:
        with self.lock:
            return self.clients.get(printer_id)

    def snapshots(self) -> List[dict]:
        configs = {p.id: p for p in self.store.list_printers()}
        out = []
        with self.lock:
            client_ids = set(self.clients.keys())
            for pid, client in self.clients.items():
                out.append(client.snapshot())
        for pid, cfg in configs.items():
            if pid not in client_ids:
                out.append({
                    "id": cfg.id,
                    "name": cfg.name,
                    "host": cfg.host,
                    "serial": cfg.serial,
                    "connected": False,
                    "registered": False,
                    "last_error": "not running" if cfg.enabled else "disabled",
                    "allow_commands": cfg.allow_commands,
                    "allow_dangerous_commands": cfg.allow_dangerous_commands,
                    "normalized": {},
                    "attributes": {},
                    "raw_status": {},
                })
        return out
