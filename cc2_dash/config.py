from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_CONFIG_PATH = Path(os.environ.get("CC2_DASH_CONFIG", "config/printers.json"))


@dataclass
class PrinterConfig:
    id: str
    name: str
    host: str
    serial: str
    access_code: str = "123456"
    port: int = 1883
    enabled: bool = True
    allow_commands: bool = False
    allow_dangerous_commands: bool = False


class ConfigStore:
    def __init__(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._printers: Dict[str, PrinterConfig] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._printers = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else data.get("printers", [])
            self._printers = {}
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                cfg = PrinterConfig(**raw)
                self._printers[cfg.id] = cfg
        except Exception:
            backup = self.path.with_suffix(self.path.suffix + f".corrupt-{int(time.time())}")
            try:
                self.path.replace(backup)
            except Exception:
                pass
            self._printers = {}

    def save(self) -> None:
        data = {"printers": [asdict(p) for p in self._printers.values()]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def list_printers(self) -> List[PrinterConfig]:
        return list(self._printers.values())

    def get(self, printer_id: str) -> Optional[PrinterConfig]:
        return self._printers.get(printer_id)

    def upsert(self, cfg: PrinterConfig) -> PrinterConfig:
        self._printers[cfg.id] = cfg
        self.save()
        return cfg

    def delete(self, printer_id: str) -> bool:
        if printer_id not in self._printers:
            return False
        del self._printers[printer_id]
        self.save()
        return True


def safe_printer_id(name_or_serial: str) -> str:
    out = []
    for ch in name_or_serial.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in ("-", "_", " "):
            out.append("-")
    value = "".join(out).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value or "cc2-printer"


def public_printer_dict(cfg: PrinterConfig, include_secret: bool = False) -> Dict[str, Any]:
    data = asdict(cfg)
    if include_secret:
        data["access_code_set"] = bool(cfg.access_code)
        return data
    data.pop("access_code", None)
    data["access_code_set"] = bool(cfg.access_code)
    return data
