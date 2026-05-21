from __future__ import annotations

import json
import socket
import time


def discover(timeout: float = 5.0, target: str = "255.255.255.255") -> list[dict]:
    payload = json.dumps({"id": 0, "method": 7000}).encode()
    results = []
    seen = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.3)
        sock.sendto(payload, (target, 52700))
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            try:
                obj = json.loads(raw.decode(errors="ignore"))
            except json.JSONDecodeError:
                continue
            result = obj.get("result") or {}
            serial = result.get("sn") or result.get("serial")
            key = (addr[0], serial)
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "ip": addr[0],
                "host_name": result.get("host_name") or result.get("hostname"),
                "machine_model": result.get("machine_model") or result.get("model"),
                "serial": serial,
                "token_status": result.get("token_status"),
                "lan_status": result.get("lan_status"),
                "raw": obj,
            })
    return results
