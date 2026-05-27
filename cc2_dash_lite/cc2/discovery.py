from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

DISCOVERY_PORT = 52700
DISCOVERY_PAYLOAD = {"id": 0, "method": 7000}


@dataclass
class DiscoveredPrinter:
    ip: str
    host_name: str = "Centauri Carbon 2"
    machine_model: str = "Centauri Carbon 2"
    serial: str = ""
    token_status: Optional[int] = None
    lan_status: Optional[int] = None
    raw: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_response(data: bytes, addr: Tuple[str, int]) -> Optional[DiscoveredPrinter]:
    try:
        obj = json.loads(data.decode("utf-8", errors="replace"))
        result = obj.get("result", {}) if isinstance(obj, dict) else {}
        if not isinstance(result, dict):
            return None
        return DiscoveredPrinter(
            ip=addr[0],
            host_name=str(result.get("host_name") or result.get("hostname") or "Centauri Carbon 2"),
            machine_model=str(result.get("machine_model") or result.get("model") or "Centauri Carbon 2"),
            serial=str(result.get("sn") or result.get("serial") or ""),
            token_status=result.get("token_status"),
            lan_status=result.get("lan_status"),
            raw=obj,
        )
    except Exception:
        return None


def discover(timeout: float = 4.0, target: str = "255.255.255.255") -> List[DiscoveredPrinter]:
    """Discover CC2 printers via UDP method 7000.

    Broadcast discovery generally requires the caller to be on the same subnet as the printer.
    Use target=<printer-ip> for directed discovery if broadcast is blocked.
    """
    printers: Dict[str, DiscoveredPrinter] = {}
    payload = json.dumps(DISCOVERY_PAYLOAD).encode("utf-8")
    deadline = time.monotonic() + max(0.5, timeout)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)
        try:
            sock.sendto(payload, (target, DISCOVERY_PORT))
        except OSError:
            # Fall back to a local broadcast address if the platform blocks 255.255.255.255.
            sock.sendto(payload, ("<broadcast>", DISCOVERY_PORT))

        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            printer = _parse_response(data, addr)
            if printer:
                key = printer.serial or printer.ip
                printers[key] = printer
    return list(printers.values())
