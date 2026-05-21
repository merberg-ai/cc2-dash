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
    camera_port: int = 8080
    camera_url_override: str | None = None
    enabled: bool = True
    allow_commands: bool = False
    allow_dangerous_commands: bool = False
    allow_experimental_commands: bool = False


class DashboardPrefs(BaseModel):
    theme: str = "dark-glass"
    startup_tab: str = "dashboard"
    refresh_ms: int = 2500
    show_camera_on_dashboard: bool = True


class AppConfig(BaseModel):
    version: int = 1
    app_name: str = "cc2-dash-v1.0"
    printers: list[PrinterConfig] = Field(default_factory=list)
    prefs: DashboardPrefs = Field(default_factory=DashboardPrefs)


class ConfigStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("CC2_DASH_CONFIG") or "config/printers.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _default(self) -> AppConfig:
        return AppConfig()

    def _read_config(self) -> AppConfig:
        if not self.path.exists():
            return self._default()
        try:
            payload = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return self._default()
        if isinstance(payload, list):
            return AppConfig(printers=[PrinterConfig(**row) for row in payload])
        if isinstance(payload, dict):
            return AppConfig(**payload)
        return self._default()

    def _write_config(self, cfg: AppConfig) -> None:
        self.path.write_text(json.dumps(cfg.model_dump(), indent=2) + "\n")

    def get_config(self) -> AppConfig:
        with self.lock:
            return self._read_config()

    def save_config(self, patch: dict[str, Any]) -> AppConfig:
        with self.lock:
            cfg = self._read_config()
            merged = cfg.model_dump()
            merged.update({k: v for k, v in patch.items() if k in {"prefs", "app_name", "version"}})
            if "printers" in patch:
                merged["printers"] = patch["printers"]
            out = AppConfig(**merged)
            self._write_config(out)
            return out

    def list_printers(self) -> list[PrinterConfig]:
        return self.get_config().printers

    def get(self, printer_id: str) -> PrinterConfig | None:
        for p in self.list_printers():
            if p.id == printer_id:
                return p
        return None

    def upsert(self, printer: PrinterConfig) -> PrinterConfig:
        with self.lock:
            cfg = self._read_config()
            found = False
            for idx, p in enumerate(cfg.printers):
                if p.id == printer.id:
                    cfg.printers[idx] = printer
                    found = True
                    break
            if not found:
                cfg.printers.append(printer)
            self._write_config(cfg)
            return printer

    def delete(self, printer_id: str) -> bool:
        with self.lock:
            cfg = self._read_config()
            filtered = [p for p in cfg.printers if p.id != printer_id]
            if len(filtered) == len(cfg.printers):
                return False
            cfg.printers = filtered
            self._write_config(cfg)
            return True
