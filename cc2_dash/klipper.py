from __future__ import annotations

import io
import time
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]

from .cc2.state import seconds_to_hms

KLIPPER_TYPES = {"klipper", "moonraker"}


class MoonrakerError(RuntimeError):
    pass


def is_klipper_printer(data: Any) -> bool:
    if data is None:
        return False
    value = getattr(data, "type", None) if not isinstance(data, dict) else (data.get("type") or data.get("printer_type"))
    return str(value or "").strip().lower() in KLIPPER_TYPES


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _clean_base_url(data: dict[str, Any]) -> str:
    url = str(data.get("moonraker_url") or "").strip()
    if url:
        if "://" not in url:
            url = "http://" + url
        return url.rstrip("/")
    host = str(data.get("host") or "").strip()
    if not host:
        return ""
    scheme = "https" if _as_bool(data.get("moonraker_https"), False) else "http"
    port = _as_int(data.get("moonraker_port") or data.get("port"), 7125)
    if "://" in host:
        parsed = urlparse(host)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return base.rstrip("/")
    return f"{scheme}://{host}:{port}".rstrip("/")


def _camera_base_url(data: dict[str, Any]) -> str:
    base = str(data.get("camera_base_url") or "").strip()
    if base:
        if "://" not in base:
            base = "http://" + base
        return base.rstrip("/")
    host = str(data.get("host") or "").strip()
    if not host:
        return ""
    if "://" in host:
        p = urlparse(host)
        return f"{p.scheme}://{p.hostname or p.netloc}".rstrip("/")
    return f"http://{host}".rstrip("/")


def resolve_url(value: Any, data: dict[str, Any], *, prefer_camera_base: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "http:" + text
    if "://" in text:
        return text
    base = _camera_base_url(data) if prefer_camera_base else _clean_base_url(data)
    if not base:
        return text
    if not text.startswith("/"):
        text = "/" + text
    return urljoin(base + "/", text.lstrip("/"))


class MoonrakerClient:
    def __init__(self, data: dict[str, Any], timeout: float = 4.0) -> None:
        self.data = data or {}
        self.base_url = _clean_base_url(self.data)
        self.timeout = float(timeout or 4.0)
        if not self.base_url:
            raise MoonrakerError("Moonraker URL/host is not configured")

    def headers(self) -> dict[str, str]:
        headers = {"User-Agent": "cc2-dash-moonraker", "Accept": "application/json"}
        token = str(self.data.get("api_key") or self.data.get("moonraker_api_key") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Api-Key"] = token
        return headers

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    def get(self, path: str, *, timeout: float | None = None, stream: bool = False) -> requests.Response:
        try:
            resp = requests.get(self._url(path), headers=self.headers(), timeout=timeout or self.timeout, stream=stream)
        except Exception as exc:
            raise MoonrakerError(str(exc)) from exc
        if resp.status_code in {401, 403}:
            raise MoonrakerError(f"Moonraker authorization failed (HTTP {resp.status_code}). Add an API key or allow this host in Moonraker.")
        if resp.status_code >= 400:
            raise MoonrakerError(f"Moonraker HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def post(self, path: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        try:
            resp = requests.post(self._url(path), json=payload or {}, headers=self.headers(), timeout=timeout or self.timeout)
        except Exception as exc:
            raise MoonrakerError(str(exc)) from exc
        if resp.status_code in {401, 403}:
            raise MoonrakerError(f"Moonraker authorization failed (HTTP {resp.status_code}).")
        if resp.status_code >= 400:
            raise MoonrakerError(f"Moonraker HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except Exception:
            return {"result": (resp.text or "ok")}

    def json(self, path: str, *, timeout: float | None = None) -> dict[str, Any]:
        resp = self.get(path, timeout=timeout)
        try:
            return resp.json()
        except Exception as exc:
            raise MoonrakerError(f"Invalid JSON from Moonraker: {exc}") from exc

    @staticmethod
    def _query_string(objects: dict[str, Any]) -> str:
        parts: list[str] = []
        for obj, attrs in objects.items():
            obj_q = quote(str(obj), safe="")
            if attrs is None:
                parts.append(obj_q)
            elif isinstance(attrs, (list, tuple, set)):
                parts.append(f"{obj_q}={quote(','.join(str(a) for a in attrs), safe=',')}")
            else:
                parts.append(f"{obj_q}={quote(str(attrs), safe=',')}")
        return "&".join(parts)

    def object_list(self) -> list[str]:
        try:
            payload = self.json("/printer/objects/list")
            result = payload.get("result") if isinstance(payload, dict) else {}
            objects = (result or {}).get("objects") or payload.get("objects") or []
            return [str(x) for x in objects]
        except Exception:
            return []

    def query_objects(self, objects: dict[str, Any]) -> dict[str, Any]:
        qs = self._query_string(objects)
        payload = self.json(f"/printer/objects/query?{qs}" if qs else "/printer/objects/query")
        result = payload.get("result") if isinstance(payload, dict) else {}
        status = (result or {}).get("status") or payload.get("status") or {}
        return status if isinstance(status, dict) else {}

    def webcams(self) -> list[dict[str, Any]]:
        try:
            payload = self.json("/server/webcams/list")
            result = payload.get("result") if isinstance(payload, dict) else {}
            webcams = (result or {}).get("webcams") or payload.get("webcams") or []
            return [w for w in webcams if isinstance(w, dict)]
        except Exception:
            return []

    def command(self, action: str) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        if action not in {"pause", "resume", "cancel"}:
            raise MoonrakerError(f"Unsupported Klipper action: {action}")
        return self.post(f"/printer/print/{action}", {}, timeout=15.0)


def _pick_chamber(status: dict[str, Any]) -> tuple[float | None, str | None]:
    preferred_terms = ("chamber", "enclosure", "ambient")
    for name, obj in status.items():
        low = str(name).lower()
        if not any(t in low for t in preferred_terms):
            continue
        if isinstance(obj, dict) and "temperature" in obj:
            return _as_float(obj.get("temperature")), name
    for name, obj in status.items():
        if str(name).startswith(("temperature_sensor ", "bme280 ", "htu21d ")) and isinstance(obj, dict) and "temperature" in obj:
            return _as_float(obj.get("temperature")), name
    return None, None


def klipper_snapshot(printer_id: str, data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    try:
        client = MoonrakerClient(data, timeout=_as_float(data.get("moonraker_timeout_seconds"), 4.0) or 4.0)
        info = client.json("/printer/info")
        objects = {
            "print_stats": None,
            "virtual_sdcard": ["progress", "is_active", "file_position"],
            "display_status": ["progress", "message"],
            "pause_resume": None,
            "idle_timeout": ["state"],
            "extruder": ["temperature", "target"],
            "heater_bed": ["temperature", "target"],
            "toolhead": ["position", "status", "homed_axes"],
        }
        for obj in [
            "temperature_sensor chamber",
            "temperature_sensor enclosure",
            "temperature_sensor ambient",
            "temperature_sensor chamber_temp",
            "bme280 chamber",
            "htu21d chamber",
        ]:
            objects[obj] = ["temperature", "humidity", "pressure"]
        status = client.query_objects(objects)
        ps = status.get("print_stats") or {}
        vsd = status.get("virtual_sdcard") or {}
        disp = status.get("display_status") or {}
        idle = status.get("idle_timeout") or {}
        pause = status.get("pause_resume") or {}
        extr = status.get("extruder") or {}
        bed = status.get("heater_bed") or {}
        toolhead = status.get("toolhead") or {}
        raw_state = str(ps.get("state") or "").strip().lower()
        klippy_state = str(((info.get("result") or info).get("state") if isinstance(info, dict) else "") or "ready").lower()
        if raw_state in {"printing", "paused", "complete", "cancelled", "error", "standby"}:
            state = raw_state
        elif klippy_state not in {"ready", "startup"}:
            state = klippy_state
        elif str(idle.get("state") or "").lower() == "printing":
            state = "printing"
        else:
            state = raw_state or "standby"
        if bool(pause.get("is_paused")):
            state = "paused"
        progress = _as_float(vsd.get("progress"), None)
        if progress is None:
            progress = _as_float(disp.get("progress"), 0.0) or 0.0
        if progress <= 1.0:
            progress *= 100.0
        progress = max(0.0, min(100.0, progress))
        filename = str(ps.get("filename") or "").strip()
        elapsed = _as_float(ps.get("print_duration"), 0.0) or 0.0
        total = _as_float(ps.get("total_duration"), 0.0) or 0.0
        remaining = max(0.0, total - elapsed) if total and total > elapsed else None
        chamber_temp, chamber_name = _pick_chamber(status)
        position = toolhead.get("position") if isinstance(toolhead.get("position"), list) else []
        normalized = {
            "state": state,
            "sub_state": "Paused" if state == "paused" else ("Printing" if state == "printing" else state.replace("_", " ").title()),
            "status_code": None,
            "sub_status_code": None,
            "progress": progress,
            "file": filename,
            "uuid": f"klipper-{printer_id}-{filename}" if filename else f"klipper-{printer_id}",
            "layers": {},
            "time": {"elapsed_sec": elapsed, "total_sec": total or None, "remaining_sec": remaining, "remaining_human": seconds_to_hms(remaining) if remaining is not None else "-"},
            "temps": {
                "nozzle": {"actual": _as_float(extr.get("temperature")), "target": _as_float(extr.get("target"), 0.0) or 0.0},
                "bed": {"actual": _as_float(bed.get("temperature")), "target": _as_float(bed.get("target"), 0.0) or 0.0},
                "chamber": {"actual": chamber_temp, "target": None, "source": chamber_name},
            },
            "position": {
                "x": _as_float(position[0]) if len(position) > 0 else None,
                "y": _as_float(position[1]) if len(position) > 1 else None,
                "z": _as_float(position[2]) if len(position) > 2 else None,
                "e": _as_float(position[3]) if len(position) > 3 else None,
            },
            "external": {"type": "klipper", "camera": True},
            "attributes": {"hostname": data.get("name") or printer_id, "machine_model": "Klipper / Moonraker", "serial": data.get("serial") or printer_id, "ip": data.get("host")},
        }
        return {
            "id": printer_id,
            "name": data.get("name") or printer_id,
            "host": data.get("host") or "",
            "serial": data.get("serial") or printer_id,
            "type": "klipper",
            "connected": True,
            "registered": True,
            "registration_error": None,
            "last_error": "",
            "last_message_age_sec": 0.1,
            "last_pong_age_sec": 0.1,
            "missed_status_count": 0,
            "allow_commands": _as_bool(data.get("allow_commands"), False),
            "allow_dangerous_commands": _as_bool(data.get("allow_dangerous_commands"), False),
            "normalized": normalized,
            "attributes": normalized["attributes"],
            "raw_status": {"moonraker_info": info, "objects": status},
            "created_epoch": now,
        }
    except Exception as exc:
        return {
            "id": printer_id,
            "name": data.get("name") or printer_id,
            "host": data.get("host") or "",
            "serial": data.get("serial") or printer_id,
            "type": "klipper",
            "connected": False,
            "registered": False,
            "registration_error": None,
            "last_error": f"Moonraker unavailable: {exc}",
            "last_message_age_sec": None,
            "last_pong_age_sec": None,
            "missed_status_count": None,
            "allow_commands": _as_bool(data.get("allow_commands"), False),
            "allow_dangerous_commands": _as_bool(data.get("allow_dangerous_commands"), False),
            "normalized": {},
            "attributes": {"hostname": data.get("name") or printer_id, "machine_model": "Klipper / Moonraker", "serial": data.get("serial") or printer_id, "ip": data.get("host")},
            "raw_status": {},
            "created_epoch": now,
        }


def klipper_camera_urls(data: dict[str, Any]) -> tuple[str, str]:
    manual_stream = resolve_url(data.get("camera_url") or data.get("direct_camera_url") or data.get("stream_url"), data)
    manual_snapshot = resolve_url(data.get("snapshot_url") or data.get("camera_snapshot_url") or data.get("direct_snapshot_url"), data)
    if manual_stream or manual_snapshot:
        return manual_stream, manual_snapshot
    try:
        client = MoonrakerClient(data, timeout=3.0)
        webcams = client.webcams()
        usable = [w for w in webcams if w.get("enabled", True) is not False]
        if usable:
            chosen = usable[0]
            stream = resolve_url(chosen.get("stream_url") or chosen.get("urlStream"), data)
            snap = resolve_url(chosen.get("snapshot_url") or chosen.get("urlSnapshot"), data)
            return stream, snap
    except Exception:
        pass
    return "", ""


def klipper_fetch_snapshot(data: dict[str, Any], timeout: float = 8.0) -> bytes:
    stream, snap = klipper_camera_urls(data)
    urls = [snap, stream]
    headers = {"User-Agent": "cc2-dash-klipper-camera", "Accept": "image/jpeg,multipart/x-mixed-replace,*/*"}
    last_error = "no Klipper camera URL configured"
    for url in [u for u in urls if u]:
        try:
            with requests.get(url, stream=True, timeout=(3.5, timeout), headers=headers) as resp:
                if resp.status_code >= 400:
                    last_error = f"HTTP {resp.status_code} from {url}"
                    continue
                ctype = (resp.headers.get("content-type") or "").lower()
                if "image" in ctype and "multipart" not in ctype:
                    data_bytes = resp.content
                    if data_bytes.startswith(b"\xff\xd8"):
                        return data_bytes
                body = bytearray()
                for chunk in resp.iter_content(chunk_size=16384):
                    if not chunk:
                        continue
                    body.extend(chunk)
                    start = body.find(b"\xff\xd8")
                    end = body.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start >= 0 and end >= 0:
                        return bytes(body[start : end + 2])
                    if len(body) > 5_000_000:
                        raise RuntimeError("camera response exceeded frame search limit")
        except Exception as exc:
            last_error = str(exc)
    raise MoonrakerError(last_error)


def klipper_mjpeg_stream(data: dict[str, Any], fps: float = 4.0):
    boundary = "cc2dashframe"
    delay = 1.0 / max(1.0, min(15.0, float(fps or 4.0)))
    while True:
        try:
            frame = klipper_fetch_snapshot(data, timeout=8.0)
        except Exception:
            frame = klipper_placeholder_frame(data)
        headers = (f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\nCache-Control: no-store\r\n\r\n").encode("ascii")
        yield headers + frame + b"\r\n"
        time.sleep(delay)


def klipper_placeholder_frame(data: dict[str, Any]) -> bytes:
    if Image is None or ImageDraw is None:
        return b"\xff\xd8\xff\xd9"
    img = Image.new("RGB", (960, 540), (12, 17, 26))
    draw = ImageDraw.Draw(img)
    name = str(data.get("name") or "Klipper")
    lines = [name, "Klipper camera unavailable", "Set camera stream/snapshot URL in Printer Manager"]
    y = 170
    for i, line in enumerate(lines):
        draw.text((60, y + i * 52), line, fill=(230, 240, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
