from __future__ import annotations

import time
from dataclasses import dataclass, field

from cc2_dash.cc2.state import deep_merge, normalize_status
from cc2_dash.config import PrinterConfig


@dataclass
class Cc2Client:
    config: PrinterConfig
    connected: bool = False
    registered: bool = False
    last_message_ts: float = 0.0
    last_pong_ts: float = 0.0
    full_status: dict = field(default_factory=dict)

    def start(self) -> None:
        self.connected = True
        self.registered = True
        now = time.time()
        self.last_message_ts = now
        self.last_pong_ts = now

    def stop(self) -> None:
        self.connected = False
        self.registered = False

    def apply_status_delta(self, delta: dict) -> None:
        self.full_status = deep_merge(self.full_status, delta)
        self.last_message_ts = time.time()

    def send_command(self, method: int, params: dict | None = None, wait: bool = True, timeout: float = 10.0) -> dict:
        _ = (wait, timeout)
        self.last_message_ts = time.time()
        if method == 1002:
            return {"id": int(time.time() * 1000), "method": 1002, "result": self.full_status}
        return {"queued": True, "id": int(time.time() * 1000), "method": method, "params": params or {}}

    def snapshot(self) -> dict:
        now = time.time()
        return {
            "printer_id": self.config.id,
            "connected": self.connected,
            "registered": self.registered,
            "last_message_age_sec": (now - self.last_message_ts) if self.last_message_ts else None,
            "last_pong_age_sec": (now - self.last_pong_ts) if self.last_pong_ts else None,
            "normalized": normalize_status(self.full_status),
            "full_status": self.full_status,
        }
