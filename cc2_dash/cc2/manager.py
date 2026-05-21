from __future__ import annotations

from cc2_dash.cc2.client import Cc2Client
from cc2_dash.config import ConfigStore


class PrinterManager:
    def __init__(self, config: ConfigStore) -> None:
        self.config = config
        self.clients: dict[str, Cc2Client] = {}

    def start_all(self) -> None:
        for printer in self.config.list_printers():
            if printer.enabled:
                self.start(printer.id)

    def stop_all(self) -> None:
        for cid in list(self.clients):
            self.stop(cid)

    def start(self, printer_id: str) -> Cc2Client:
        printer = self.config.get(printer_id)
        if not printer:
            raise KeyError(printer_id)
        client = Cc2Client(printer)
        client.start()
        self.clients[printer_id] = client
        return client

    def stop(self, printer_id: str) -> None:
        client = self.clients.pop(printer_id, None)
        if client:
            client.stop()

    def restart(self, printer_id: str) -> Cc2Client:
        self.stop(printer_id)
        return self.start(printer_id)

    def get_client(self, printer_id: str) -> Cc2Client | None:
        return self.clients.get(printer_id)

    def ensure_client(self, printer_id: str) -> Cc2Client:
        return self.get_client(printer_id) or self.start(printer_id)
