from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field


class PrinterConfig(BaseModel):
    id: str
    name: str
    host: str
    serial: str
    access_code: str
    port: int = 1883
    enabled: bool = True
    allow_commands: bool = False
    allow_dangerous_commands: bool = False


class ConfigStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("CC2_DASH_CONFIG") or "config/printers.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _read_raw(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _write_raw(self, rows: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(rows, indent=2) + "\n")

    def list_printers(self) -> list[PrinterConfig]:
        with self.lock:
            return [PrinterConfig(**row) for row in self._read_raw()]

    def get(self, printer_id: str) -> PrinterConfig | None:
        with self.lock:
            for row in self._read_raw():
                if row.get("id") == printer_id:
                    return PrinterConfig(**row)
            return None

    def upsert(self, printer: PrinterConfig) -> PrinterConfig:
        with self.lock:
            rows = self._read_raw()
            replaced = False
            for i, row in enumerate(rows):
                if row.get("id") == printer.id:
                    rows[i] = printer.model_dump()
                    replaced = True
                    break
            if not replaced:
                rows.append(printer.model_dump())
            self._write_raw(rows)
            return printer

    def delete(self, printer_id: str) -> bool:
        with self.lock:
            rows = self._read_raw()
            new_rows = [row for row in rows if row.get("id") != printer_id]
            changed = len(new_rows) != len(rows)
            if changed:
                self._write_raw(new_rows)
            return changed
