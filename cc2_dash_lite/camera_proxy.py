from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import requests

from . import __version__
from .config import PrinterConfig, printer_dict_to_config
from .logger import log

BOUNDARY = "cc2dashframe"
import base64

DEFAULT_PLACEHOLDER_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD5/ooooA//2Q=="
)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _as_float(value: Any, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        out = float(value)
    except Exception:
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def camera_proxy_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    cfg = cfg or {}
    raw = cfg.get("camera_proxy") if "camera_proxy" in cfg else cfg
    raw = raw or {}
    return {
        "enabled": _as_bool(raw.get("enabled"), True),
        "start_on_boot": _as_bool(raw.get("start_on_boot"), True),
        "max_client_fps": _as_float(raw.get("max_client_fps"), 8.0, 1.0, 30.0),
        "upstream_connect_timeout_seconds": _as_float(raw.get("upstream_connect_timeout_seconds"), 5.0, 1.0, 30.0),
        "upstream_read_timeout_seconds": _as_float(raw.get("upstream_read_timeout_seconds"), 20.0, 5.0, 120.0),
        "stale_frame_seconds": _as_float(raw.get("stale_frame_seconds"), 10.0, 2.0, 300.0),
        "idle_shutdown_seconds": _as_float(raw.get("idle_shutdown_seconds"), 120.0, 0.0, 86400.0),
        "fallback_to_direct": _as_bool(raw.get("fallback_to_direct"), False),
        "rewrite_portal_camera_urls": _as_bool(raw.get("rewrite_portal_camera_urls"), True),
        "log_client_connects": _as_bool(raw.get("log_client_connects"), False),
    }


@dataclass
class CameraRelay:
    printer_id: str
    pcfg: PrinterConfig
    _thread: threading.Thread | None = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _wake: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()), init=False)
    _latest_frame: bytes | None = field(default=None, init=False)
    _latest_epoch: float | None = field(default=None, init=False)
    _latest_seq: int = field(default=0, init=False)
    _clients: int = field(default=0, init=False)
    _started_epoch: float | None = field(default=None, init=False)
    _upstream_connected: bool = field(default=False, init=False)
    _upstream_url: str | None = field(default=None, init=False)
    _last_error: str | None = field(default=None, init=False)
    _last_connect_epoch: float | None = field(default=None, init=False)
    _last_disconnect_epoch: float | None = field(default=None, init=False)
    _frames_received: int = field(default=0, init=False)
    _bytes_received: int = field(default=0, init=False)
    _reconnects: int = field(default=0, init=False)
    _last_client_epoch: float | None = field(default=None, init=False)
    _last_config: dict[str, Any] = field(default_factory=dict, init=False)

    def update_printer(self, pcfg: PrinterConfig) -> None:
        changed = pcfg.host != self.pcfg.host or pcfg.id != self.pcfg.id
        self.pcfg = pcfg
        if changed:
            self.restart(self._last_config or {})

    def urls(self) -> list[str]:
        return [f"http://{self.pcfg.host}:8080/", f"http://{self.pcfg.host}:8080/?action=stream"]

    def start(self, cfg: dict[str, Any] | None = None) -> None:
        c = camera_proxy_config(cfg)
        self._last_config = c
        if not c["enabled"]:
            self.stop()
            return
        with self._wake:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, args=(c,), name=f"cc2-camera-relay-{self.printer_id}", daemon=True)
            self._started_epoch = time.time()
            self._thread.start()
        log("info", f"Camera relay started for {self.printer_id}", "camera", printer=self.printer_id)

    def stop(self) -> None:
        self._stop.set()
        with self._wake:
            self._wake.notify_all()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._wake:
            self._thread = None
            self._upstream_connected = False
            self._last_disconnect_epoch = time.time()
            self._wake.notify_all()

    def restart(self, cfg: dict[str, Any] | None = None) -> None:
        self.stop()
        time.sleep(0.05)
        self.start(cfg or self._last_config or {})
        log("warning", f"Camera relay restarted for {self.printer_id}", "camera", printer=self.printer_id)

    def _set_error(self, error: str) -> None:
        with self._wake:
            self._last_error = error
            self._upstream_connected = False
            self._last_disconnect_epoch = time.time()
            self._wake.notify_all()

    def _set_frame(self, frame: bytes, url: str) -> None:
        now = time.time()
        with self._wake:
            was_down = not self._upstream_connected
            self._latest_frame = frame
            self._latest_epoch = now
            self._latest_seq += 1
            self._frames_received += 1
            self._bytes_received += len(frame)
            self._upstream_connected = True
            self._upstream_url = url
            self._last_error = None
            self._wake.notify_all()
        if was_down:
            log("info", f"Camera relay connected upstream for {self.printer_id}: {url}", "camera", printer=self.printer_id)

    def _should_idle_stop(self, cfg: dict[str, Any]) -> bool:
        idle = float(cfg.get("idle_shutdown_seconds") or 0)
        if idle <= 0 or cfg.get("start_on_boot"):
            return False
        with self._wake:
            if self._clients > 0:
                return False
            last = self._last_client_epoch or self._started_epoch or time.time()
        return time.time() - last > idle

    def _run(self, cfg: dict[str, Any]) -> None:
        backoff = 1.0
        buffer_limit = 6_000_000
        headers = {
            "User-Agent": "cc2-dash-lite-camera-relay/" + __version__,
            "Accept": "multipart/x-mixed-replace,image/jpeg,*/*",
            "Cache-Control": "no-cache",
            "Connection": "close",
        }
        while not self._stop.is_set():
            if self._should_idle_stop(cfg):
                log("info", f"Camera relay idle stop for {self.printer_id}", "camera", printer=self.printer_id)
                with self._wake:
                    self._thread = None
                    self._upstream_connected = False
                    self._wake.notify_all()
                return
            last_error = "no upstream URL tried"
            for url in self.urls():
                if self._stop.is_set():
                    break
                try:
                    with self._wake:
                        self._last_connect_epoch = time.time()
                        self._upstream_url = url
                    with requests.get(
                        url,
                        stream=True,
                        timeout=(float(cfg["upstream_connect_timeout_seconds"]), float(cfg["upstream_read_timeout_seconds"])),
                        headers=headers,
                    ) as resp:
                        if resp.status_code >= 400:
                            last_error = f"HTTP {resp.status_code} from {url}"
                            continue
                        backoff = 1.0
                        body = bytearray()
                        for chunk in resp.iter_content(chunk_size=16384):
                            if self._stop.is_set():
                                break
                            if not chunk:
                                continue
                            body.extend(chunk)
                            # MJPEG streams are just JPEGs back-to-back with text boundaries.
                            while True:
                                start = body.find(b"\xff\xd8")
                                if start < 0:
                                    if len(body) > 8192:
                                        del body[:-1024]
                                    break
                                end = body.find(b"\xff\xd9", start + 2)
                                if end < 0:
                                    if start > 0:
                                        del body[:start]
                                    break
                                frame = bytes(body[start : end + 2])
                                del body[: end + 2]
                                if len(frame) > 128:
                                    self._set_frame(frame, url)
                            if len(body) > buffer_limit:
                                start = body.rfind(b"\xff\xd8")
                                if start >= 0:
                                    del body[:start]
                                else:
                                    body.clear()
                                    raise RuntimeError("camera relay buffer exceeded capture limit")
                    if self._stop.is_set():
                        break
                    last_error = f"stream ended from {url}"
                except Exception as exc:
                    last_error = str(exc)
            if self._stop.is_set():
                break
            self._reconnects += 1
            self._set_error(last_error)
            log("warning", f"Camera relay reconnecting for {self.printer_id}: {last_error}", "camera", printer=self.printer_id)
            self._stop.wait(backoff)
            backoff = min(30.0, backoff * 1.6)
        self._set_error("relay stopped")

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._wake:
            age = None if self._latest_epoch is None else now - self._latest_epoch
            stale_after = float((self._last_config or {}).get("stale_frame_seconds") or 10.0)
            running = bool(self._thread and self._thread.is_alive())
            return {
                "ok": bool(running and self._latest_frame and age is not None and age <= stale_after),
                "enabled": bool((self._last_config or {}).get("enabled", True)),
                "running": running,
                "upstream_connected": self._upstream_connected,
                "upstream_url": self._upstream_url,
                "client_count": self._clients,
                "last_frame_epoch": self._latest_epoch,
                "last_frame_age_seconds": None if age is None else round(age, 3),
                "stale": bool(age is None or age > stale_after),
                "stale_after_seconds": stale_after,
                "frames_received": self._frames_received,
                "bytes_received": self._bytes_received,
                "reconnects": self._reconnects,
                "last_error": self._last_error,
                "last_connect_epoch": self._last_connect_epoch,
                "last_disconnect_epoch": self._last_disconnect_epoch,
                "max_client_fps": (self._last_config or {}).get("max_client_fps"),
            }

    def latest_frame(self, cfg: dict[str, Any] | None = None, max_age: float | None = None, wait_timeout: float = 8.0) -> bytes:
        c = camera_proxy_config(cfg or self._last_config or {})
        self.start(c)
        deadline = time.time() + max(0.1, wait_timeout)
        with self._wake:
            while not self._latest_frame and time.time() < deadline and not self._stop.is_set():
                self._wake.wait(timeout=max(0.05, deadline - time.time()))
            if not self._latest_frame:
                raise RuntimeError(self._last_error or "camera relay has no frame yet")
            age = time.time() - float(self._latest_epoch or 0)
            if max_age is not None and age > max_age:
                raise RuntimeError(f"camera relay frame is stale ({age:.1f}s old)")
            return bytes(self._latest_frame)

    def stream(self, cfg: dict[str, Any] | None = None) -> Iterable[bytes]:
        c = camera_proxy_config(cfg or self._last_config or {})
        self.start(c)
        min_delay = 1.0 / max(1.0, float(c.get("max_client_fps") or 8.0))
        stale_after = max(2.0, float(c.get("stale_frame_seconds") or 10.0))
        log_clients = bool(c.get("log_client_connects"))
        with self._wake:
            self._clients += 1
            self._last_client_epoch = time.time()
            if log_clients:
                log("info", f"Camera relay client connected for {self.printer_id}; clients={self._clients}", "camera", printer=self.printer_id)
            self._wake.notify_all()
        last_seq = -1
        last_send = 0.0
        try:
            while not self._stop.is_set():
                with self._wake:
                    end_wait = time.time() + max(1.0, stale_after)
                    while self._latest_seq == last_seq and not self._stop.is_set() and time.time() < end_wait:
                        self._wake.wait(timeout=min(1.0, max(0.05, end_wait - time.time())))
                    frame = self._latest_frame or DEFAULT_PLACEHOLDER_JPEG
                    seq = self._latest_seq
                    last_error = self._last_error
                # If the relay is still warming up, send a tiny valid JPEG occasionally
                # so browsers keep the <img> alive instead of surfacing a hard error.
                now = time.time()
                sleep_for = min_delay - (now - last_send)
                if sleep_for > 0:
                    time.sleep(sleep_for)
                headers = (
                    f"--{BOUNDARY}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n"
                    "Cache-Control: no-store\r\n\r\n"
                ).encode("ascii")
                yield headers + frame + b"\r\n"
                last_send = time.time()
                last_seq = seq
                if last_error and frame == DEFAULT_PLACEHOLDER_JPEG:
                    time.sleep(1.0)
        finally:
            with self._wake:
                self._clients = max(0, self._clients - 1)
                self._last_client_epoch = time.time()
                if log_clients:
                    log("info", f"Camera relay client disconnected for {self.printer_id}; clients={self._clients}", "camera", printer=self.printer_id)
                self._wake.notify_all()


class CameraRelayManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._relays: dict[str, CameraRelay] = {}

    def get(self, printer_id: str, pcfg: PrinterConfig) -> CameraRelay:
        with self._lock:
            relay = self._relays.get(printer_id)
            if relay is None:
                relay = CameraRelay(printer_id, pcfg)
                self._relays[printer_id] = relay
            else:
                relay.update_printer(pcfg)
            return relay

    def configure_from_config(self, cfg: dict[str, Any]) -> None:
        c = camera_proxy_config(cfg)
        printers = cfg.get("printers") or {}
        with self._lock:
            keep = set(printers.keys())
            for printer_id, pdata in printers.items():
                pcfg = printer_dict_to_config(printer_id, pdata)
                relay = self.get(printer_id, pcfg)
                relay._last_config = c
                if c["enabled"] and c["start_on_boot"] and (pdata or {}).get("enabled", True):
                    relay.start(c)
                elif not c["enabled"]:
                    relay.stop()
            for printer_id in list(self._relays.keys()):
                if printer_id not in keep:
                    self._relays.pop(printer_id).stop()

    def stop_all(self) -> None:
        with self._lock:
            relays = list(self._relays.values())
        for relay in relays:
            relay.stop()

    def status_all(self) -> dict[str, Any]:
        with self._lock:
            return {pid: relay.status() for pid, relay in self._relays.items()}


camera_relays = CameraRelayManager()


def rewrite_camera_urls(content: str, pcfg: PrinterConfig, printer_id: str | None = None) -> str:
    """Rewrite common stock-portal camera references to the local relay.

    The stock portal bundle can refer to the CC2 camera by absolute URL, by
    protocol-relative URL, or by simple camera-ish paths. This keeps the portal
    from opening extra upstream camera sockets when it is rendered through
    cc2-dash-lite.
    """
    pid = printer_id or pcfg.id
    relay = f"/api/printers/{pid}/camera/stream"
    host = re.escape(pcfg.host)
    patterns = [
        rf"https?://{host}:8080/\?action=stream",
        rf"https?://{host}:8080/",
        rf"//{host}:8080/\?action=stream",
        rf"//{host}:8080/",
    ]
    for pat in patterns:
        content = re.sub(pat, relay, content)
    # Relative paths only get rewritten inside proxied portal assets. Avoid
    # rewriting arbitrary words; stick to quoted/url() contexts.
    relative_pairs = [
        ('"/camera"', f'"{relay}"'),
        ("'/camera'", f"'{relay}'"),
        ('"/stream"', f'"{relay}"'),
        ("'/stream'", f"'{relay}'"),
        ('"/webcam"', f'"{relay}"'),
        ("'/webcam'", f"'{relay}'"),
        ('"/?action=stream"', f'"{relay}"'),
        ("'/?action=stream'", f"'{relay}'"),
    ]
    for old, new in relative_pairs:
        content = content.replace(old, new)
    return content
