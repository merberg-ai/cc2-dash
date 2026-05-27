from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, asdict
from typing import Iterable

import httpx

from .logger import log


@dataclass
class ScanCandidate:
    host: str
    open_ports: list[int]
    http_title: str | None = None
    likely_printer: bool = False
    notes: list[str] | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["portal_url"] = f"http://{self.host}/"
        data["camera_url"] = f"http://{self.host}:8080/"
        return data


def expand_targets(subnet_or_host: str, max_hosts: int = 512) -> list[str]:
    subnet_or_host = (subnet_or_host or "").strip()
    if not subnet_or_host:
        subnet_or_host = "192.168.1.0/24"
    try:
        if "/" in subnet_or_host:
            net = ipaddress.ip_network(subnet_or_host, strict=False)
            return [str(ip) for ip in list(net.hosts())[:max_hosts]]
        ipaddress.ip_address(subnet_or_host)
        return [subnet_or_host]
    except ValueError:
        # Best effort for shorthand like 192.168.1.x
        if subnet_or_host.endswith(".x"):
            prefix = subnet_or_host[:-2]
            return [f"{prefix}.{i}" for i in range(1, 255)]
        raise


async def _tcp_check(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _http_title(host: str, ports: Iterable[int], timeout: float = 0.8) -> tuple[str | None, list[str]]:
    notes: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for port in ports:
            if port not in (80, 8080, 3030, 5000, 8000, 8088, 8899):
                continue
            url = f"http://{host}:{port}/" if port != 80 else f"http://{host}/"
            try:
                resp = await client.get(url)
                text = resp.text[:4096]
                lower = text.lower()
                title = None
                if "<title" in lower:
                    start = lower.find("<title")
                    start = lower.find(">", start) + 1
                    end = lower.find("</title", start)
                    if start > 0 and end > start:
                        title = text[start:end].strip()[:120]
                if any(word in lower for word in ["elegoo", "centauri", "printer", "camera", "mjpeg", "fluidd", "mainsail"]):
                    notes.append(f"HTTP:{port} looked printer-ish")
                return title or f"HTTP {resp.status_code} on port {port}", notes
            except Exception:
                continue
    return None, notes


async def scan_host(host: str, ports: list[int], sem: asyncio.Semaphore) -> ScanCandidate | None:
    async with sem:
        checks = await asyncio.gather(*[_tcp_check(host, int(port)) for port in ports])
        open_ports = [int(port) for port, is_open in zip(ports, checks) if is_open]
        if not open_ports:
            return None
        title, notes = await _http_title(host, open_ports)
        # Be intentionally conservative here. A router, Tasmota device, NAS,
        # camera, etc. can expose port 80/8080, so "port is open" is not enough
        # to present it as a printer candidate in the UI. Real Centauri Carbon 2
        # discovery is handled by the UDP method-7000 probe in main.py; this
        # generic scan is only a fallback helper for hosts that look worth a
        # directed CC2 verification attempt.
        note_words = " ".join(notes).lower()
        title_words = (title or "").lower()
        cc2_ports = 1883 in open_ports and (8080 in open_ports or 80 in open_ports)
        likely = cc2_ports or any(word in title_words for word in ["elegoo", "centauri"])
        if any(word in note_words for word in ["elegoo", "centauri"]):
            likely = True
        return ScanCandidate(host=host, open_ports=open_ports, http_title=title, likely_printer=likely, notes=notes or [])


async def scan_network(subnet_or_host: str, ports: list[int], concurrency: int = 64) -> list[dict]:
    targets = expand_targets(subnet_or_host)
    ports = [int(p) for p in ports]
    log("info", f"Scanning {len(targets)} host(s) on ports {ports}", "scanner")
    sem = asyncio.Semaphore(concurrency)
    tasks = [scan_host(host, ports, sem) for host in targets]
    found: list[dict] = []
    for coro in asyncio.as_completed(tasks):
        candidate = await coro
        if candidate:
            log("info", f"Found host {candidate.host} ports={candidate.open_ports}", "scanner")
            found.append(candidate.to_dict())
    found.sort(key=lambda c: (not c.get("likely_printer", False), c["host"]))
    log("info", f"Scan complete: {len(found)} candidate(s)", "scanner")
    return found


def local_ip_guess() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return None


def default_subnet_guess() -> str:
    ip = local_ip_guess()
    if not ip:
        return "192.168.1.0/24"
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".0/24"
    return "192.168.1.0/24"
