from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

import httpx
import requests
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__
from .config import (
    APP_ROOT,
    DATA_DIR,
    default_printer,
    load_config,
    needs_setup,
    printer_dict_to_config,
    public_printer_dict,
    safe_printer_id,
    save_config,
    sorted_actions,
    sorted_cards,
)
from .logger import get_logs, log, log_sources
from .printer_client import PrinterClient
from .scanner import default_subnet_guess, scan_network
from .themes import FONT_STACKS, THEMES, get_theme, theme_css_vars
from .cc2.commands import (
    DELETE_FILE,
    ENABLE_WEBCAM,
    GET_CANVAS_STATUS,
    GET_MONO_FILAMENT_INFO,
    GET_DISK_INFO,
    GET_FILE_DETAIL,
    GET_FILE_LIST,
    GET_FILE_THUMBNAIL,
    GET_HISTORY_TASK,
    GET_HISTORY_TASK_DETAIL,
    GET_TIME_LAPSE_VIDEO_LIST,
    LOAD_FILAMENT,
    SET_FILAMENT_INFO,
    SET_MONO_FILAMENT_INFO,
    PAUSE_PRINT,
    RESUME_PRINT,
    START_PRINT,
    HISTORY_DELETE,
    SET_LIGHT,
    SET_AUTO_REFILL,
    UNLOAD_FILAMENT,
    SET_PRINT_SPEED,
    START_VIDEO_STREAM,
    STOP_PRINT,
    delete_file_params,
    history_delete_params,
    history_detail_params,
    file_detail_params,
    file_list_params,
    file_thumbnail_params,
    normalize_file_dir,
    normalize_storage_media,
    start_print_params,
    timelapse_export_params,
    auto_refill_params,
    filament_info_params,
    filament_motion_params,
    mono_filament_info_params,
    light_params,
    method_allowed,
    print_speed_params,
    webcam_params,
)
# Import CommandError from the client module explicitly for clarity.
from .cc2.client import CommandError
from .cc2.discovery import discover
from .cc2.runtime import Cc2PrinterRuntime
from .cc2.state import seconds_to_hms
from .ai import portal_ai
from . import ai_learning
from .build_info import get_build_info
from .camera_proxy import camera_proxy_config, camera_relays, rewrite_camera_urls
from .feedback_learning import (
    current_suppressions,
    feedback_stats,
    interpret_feedback,
    record_feedback_suppression,
)
from .vision import vision_monitor

app = FastAPI(title="cc2-dash", version=__version__)
app.mount("/static", StaticFiles(directory=str(APP_ROOT / "static")), name="static")
ELEGEEGO_WEB_DIR = Path(__file__).resolve().parent / "elegoo_web"
if ELEGEEGO_WEB_DIR.exists():
    app.mount("/elegoo", StaticFiles(directory=str(ELEGEEGO_WEB_DIR), html=True), name="elegoo")
templates = Jinja2Templates(directory=str(APP_ROOT / "templates"))
runtime = Cc2PrinterRuntime()
_AI_MONITOR_TASK: asyncio.Task | None = None
_AI_MONITOR_STATE: dict[str, Any] = {
    "running": False,
    "iterations": 0,
    "last_loop_epoch": None,
    "last_loop": None,
    "last_error": None,
}
_AI_MONITOR_LAST_LOGGED: dict[str, dict[str, Any]] = {}

SPEED_PRESETS = {
    0: "Silent",
    1: "Balanced",
    2: "Sport",
    3: "Ludicrous",
}


ACTIVE_MACHINE_STATUS_CODES = {2}
ACTIVE_SUB_STATUS_CODES = {
    1041,  # idle in print / active job context
    1045, 1096,  # extruder preheating during a queued/active print
    1405, 1906,  # bed preheating during a queued/active print
    2075,  # printing
    2401, 2402,  # resuming / resume complete
    2501, 2502, 2503, 2504, 2505,  # pause/stop states while a job exists
}
IDLE_MACHINE_STATUS_CODES = {1, 16}
IDLE_SUB_STATUS_CODES = {0, 2077}


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _has_real_file(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"-", "none", "None", "null"})


def _status_looks_active_print(status: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> bool:
    """Best-effort active job detector used to gate AI/vision work.

    The CC2 can leave old file/progress values around after a job completes, so
    raw file name alone is not enough. Prefer explicit machine/sub status codes,
    then fall back to state text plus print markers.
    """
    status = status or {}
    n = ((snap or {}).get("normalized") or {}) if isinstance(snap, dict) else {}
    machine_code = n.get("status_code")
    sub_code = n.get("sub_status_code")
    try:
        machine_code = int(machine_code) if machine_code is not None else None
    except Exception:
        machine_code = None
    try:
        sub_code = int(sub_code) if sub_code is not None else None
    except Exception:
        sub_code = None

    file_name = status.get("file") if status.get("file") is not None else n.get("file")
    has_file = _has_real_file(file_name)
    progress = _coerce_float(status.get("progress", n.get("progress", 0.0)), 0.0)
    elapsed = _coerce_float(((n.get("time") or {}).get("elapsed_sec")), 0.0)
    hot_target = _coerce_float(status.get("hotend_target", ((n.get("temps") or {}).get("nozzle") or {}).get("target")), 0.0)
    bed_target = _coerce_float(status.get("bed_target", ((n.get("temps") or {}).get("bed") or {}).get("target")), 0.0)
    state_text = " ".join(
        str(x or "")
        for x in (
            status.get("state"),
            status.get("status_text"),
            n.get("state"),
            n.get("sub_state"),
        )
    ).lower()

    if machine_code in ACTIVE_MACHINE_STATUS_CODES:
        return True
    if sub_code in ACTIVE_SUB_STATUS_CODES and (has_file or machine_code not in IDLE_MACHINE_STATUS_CODES):
        return True
    if machine_code in IDLE_MACHINE_STATUS_CODES and sub_code in IDLE_SUB_STATUS_CODES:
        return False
    if "completed" in state_text or state_text.strip() == "idle":
        return False
    if any(word in state_text for word in ("printing", "paused", "pausing", "resuming", "stopping", "idle in print")):
        return True
    if has_file and 0.0 < progress < 99.9:
        return True
    if has_file and elapsed > 0 and progress < 99.9 and (hot_target > 0 or bed_target > 0):
        return True
    return False


def _idle_vision_result(printer_id: str, source: str = "request") -> dict[str, Any]:
    now = time.time()
    result = {
        "enabled": True,
        "skipped": True,
        "visual_state": "standby",
        "summary": "Printer is idle; vision monitoring is paused until an active print starts.",
        "consecutive_bad": 0,
        "last_check_epoch": now,
        "last_check": time.strftime("%H:%M:%S"),
        "source": source,
        "active_print": False,
    }
    return vision_monitor.set_cached_result(printer_id, result)


def _idle_ai_result(printer_id: str, status: dict[str, Any], cfg: dict[str, Any], source: str = "request") -> dict[str, Any]:
    ai_cfg = cfg.get("portal_ai", {}) or {}
    now = time.time()
    vision = status.get("vision_ai") if isinstance(status.get("vision_ai"), dict) else None
    result = {
        "enabled": bool(ai_cfg.get("enabled", True)),
        "state": "idle_standby",
        "level": "low",
        "risk": 0,
        "summary": "Idle",
        "reasons": ["Printer is idle; AI watchdog and vision monitoring are paused until an active print starts."],
        "positives": ["Printer status is idle."],
        "active_print": False,
        "monitor_active_prints_only": True,
        "last_check_epoch": now,
        "last_check": time.strftime("%H:%M:%S"),
        "source": source,
        "background_monitor_enabled": bool(ai_cfg.get("background_monitor_enabled", True)),
        "rules": {
            "telemetry": bool(ai_cfg.get("telemetry_rules_enabled", True)),
            "camera": bool(ai_cfg.get("camera_rules_enabled", True)),
            "vision": bool(ai_cfg.get("vision_ai_enabled", False)),
        },
        "vision": vision,
    }
    return portal_ai.set_cached_result(printer_id, result)

FILAMENT_TRAY_STATUS = {
    # Matches the stock portal enum: Empty=0, preViewLoad=1, loaded=2.
    0: "empty",
    1: "preview load",
    2: "loaded",
    3: "ready",
    4: "rfid detecting",
    5: "busy",
}


def _dig(data: Any, *keys: str, default: Any = None) -> Any:
    """Return the first matching key from a dict, accepting common case variants."""
    if not isinstance(data, dict):
        return default
    for key in keys:
        variants = {
            key,
            key.lower(),
            key.upper(),
            key[:1].lower() + key[1:] if key else key,
            key[:1].upper() + key[1:] if key else key,
        }
        for variant in variants:
            if variant in data:
                return data[variant]
    return default


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _find_first_key(node: Any, *keys: str, depth: int = 0, max_depth: int = 5) -> Any:
    """Find the first exact-ish key match in a small nested status blob."""
    if node is None or depth > max_depth:
        return None
    wanted = set()
    for key in keys:
        if not key:
            continue
        wanted.update({key, key.lower(), key.upper(), key[:1].lower() + key[1:], key[:1].upper() + key[1:]})
    if isinstance(node, dict):
        for key, value in node.items():
            if key in wanted:
                return value
        for value in node.values():
            found = _find_first_key(value, *keys, depth=depth + 1, max_depth=max_depth)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node[:16]:
            found = _find_first_key(item, *keys, depth=depth + 1, max_depth=max_depth)
            if found is not None:
                return found
    return None


def _find_filament_root(node: Any, depth: int = 0) -> dict[str, Any] | None:
    """Find the stock-style MMS/filament object inside raw CC2 status blobs.

    Elegoo's web tooling works with an object shaped like
    {mmsSystemName, mmsList:[{trayList:[...]}]}. The firmware has exposed that
    through slightly different wrappers in different places, so this walks a
    small JSON tree looking for mmsList/trayList rather than hard-coding one
    exact path.
    """
    if depth > 6:
        return None
    if isinstance(node, dict):
        if any(k in node for k in ("mmsList", "MmsList", "mms_list", "canvasList", "canvas_list", "CanvasList", "trayList", "TrayList", "tray_list")):
            return node
        preferred = ["canvas", "canvas_info", "canvasInfo", "mmsInfo", "mms_info", "mms", "ams", "filament", "filaments", "result", "data"]
        for key in preferred:
            if key in node:
                found = _find_filament_root(node[key], depth + 1)
                if found:
                    return found
        for value in node.values():
            found = _find_filament_root(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for item in node[:12]:
            found = _find_filament_root(item, depth + 1)
            if found:
                return found
    return None


def _boolish(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "enable", "filament", "detected", "present", "loaded", "load"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable", "none", "empty", "no_filament", "nofilament", "runout", "/"}:
        return False
    return None


def _color_value(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        color = value.strip()
        if not color.startswith("#") and len(color) in (3, 6):
            color = "#" + color
        return color
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return "#%02x%02x%02x" % (int(value[0]), int(value[1]), int(value[2]))
        except Exception:
            pass
    return "#8b8f9a"


def _normalize_tray(tray: dict[str, Any], mms_id: str = "", index: int = 0) -> dict[str, Any]:
    status_raw = _dig(tray, "status", "trayStatus", "TrayStatus", "state", "tray_state")
    try:
        status_code = int(float(status_raw)) if status_raw not in (None, "") else None
    except Exception:
        status_code = None
    tray_id = _dig(tray, "trayId", "tray_id", "slotId", "slot_id", "id", "Id", default=str(index))
    try:
        slot_number = int(float(tray_id)) + 1 if int(float(tray_id)) in (0, 1, 2, 3) else int(float(tray_id))
    except Exception:
        slot_number = index + 1
    name = _dig(tray, "trayName", "tray_name", "slotName", "slot_name", "name", "Name", default=f"Slot {slot_number}")
    ftype = _dig(tray, "filamentType", "filament_type", "type", "material", "Material", default="")
    fname = _dig(tray, "filamentName", "filament_name", "name", "displayName", "display_name", "settingName", "setting_name", default="")
    color = _color_value(_dig(tray, "filamentColor", "filament_color", "filamentColour", "filament_colour", "color", "Colour", "Color"))
    vendor = _dig(tray, "vendor", "brand", "filamentBrand", "filament_brand", "manufacturer", default="")
    active = status_code in (1, 2, 3) or bool(ftype or fname)
    return {
        "mms_id": str(_dig(tray, "mmsId", "mms_id", "canvasId", "canvas_id", default=mms_id) or mms_id),
        "canvas_id": str(_dig(tray, "canvasId", "canvas_id", "mmsId", "mms_id", default=mms_id if str(mms_id).isdigit() else "0") or "0"),
        "tray_id": str(tray_id if tray_id not in (None, "") else index),
        "tray_name": str(name or f"Slot {slot_number}"),
        "slot_number": slot_number,
        "filament_type": str(ftype or ""),
        "filament_name": str(fname or ""),
        "filament_color": color,
        "vendor": str(vendor or ""),
        "serial_number": str(_dig(tray, "serialNumber", "sn", "serial", default="") or ""),
        "brand": str(vendor or ""),
        "status": status_code,
        "status_label": FILAMENT_TRAY_STATUS.get(status_code, f"status {status_code}" if status_code is not None else ("active" if active else "unknown")),
        "active": active,
        "weight_g": _dig(tray, "filamentWeight", "weight", "remain", "remaining", default=None),
        "density": _dig(tray, "filamentDensity", "density", default=None),
        "diameter": _dig(tray, "filamentDiameter", "diameter", default=None),
        "min_nozzle_temp": _dig(tray, "minNozzleTemp", "nozzleTempMin", "filament_min_temp", "filamentMinTemp", default=None),
        "max_nozzle_temp": _dig(tray, "maxNozzleTemp", "nozzleTempMax", "filament_max_temp", "filamentMaxTemp", default=None),
        "min_bed_temp": _dig(tray, "minBedTemp", "bedTempMin", default=None),
        "max_bed_temp": _dig(tray, "maxBedTemp", "bedTempMax", default=None),
        "setting_id": str(_dig(tray, "settingId", "setting_id", "filamentId", "filament_code", default="") or ""),
        "filament_code": str(_dig(tray, "filamentCode", "filament_code", "settingId", default="") or ""),
        "raw": tray,
    }


def _filament_idle_state(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = snapshot or {}
    n = (snapshot.get("normalized") or {}) if isinstance(snapshot, dict) else {}
    state = str(n.get("state") or "unknown")
    sub_state = str(n.get("sub_state") or "")
    status_stub = {
        "state": state,
        "status_text": sub_state,
        "progress": n.get("progress"),
        "file": n.get("file"),
        "hotend_target": (((n.get("temps") or {}).get("nozzle") or {}).get("target")),
        "bed_target": (((n.get("temps") or {}).get("bed") or {}).get("target")),
    }
    active_print = _status_looks_active_print(status_stub, snapshot)
    machine_code = n.get("status_code")
    sub_code = n.get("sub_status_code")
    try:
        machine_code = int(machine_code) if machine_code is not None else None
    except Exception:
        machine_code = None
    try:
        sub_code = int(sub_code) if sub_code is not None else None
    except Exception:
        sub_code = None
    state_text = f"{state} {sub_state}".strip().lower()
    explicit_idle = (machine_code in IDLE_MACHINE_STATUS_CODES and (sub_code is None or sub_code in IDLE_SUB_STATUS_CODES)) or ("idle" in state_text and "print" not in state_text) or ("completed" in state_text)
    filament_busy = any(word in state_text for word in ("filament operating", "extruder operating", "preheating", "loading", "unloading"))
    printer_idle = bool(explicit_idle and not active_print and not filament_busy)
    return {
        "active_print": bool(active_print),
        "printer_idle": printer_idle,
        "state": state,
        "sub_state": sub_state,
        "status_code": machine_code,
        "sub_status_code": sub_code,
    }


def _require_filament_idle(printer_id: str) -> dict[str, Any]:
    snap = runtime.snapshot(printer_id) or {}
    idle = _filament_idle_state(snap)
    if not idle.get("printer_idle"):
        label = " / ".join(x for x in (idle.get("state"), idle.get("sub_state")) if x) or "not idle"
        raise HTTPException(409, f"Filament load/unload/edit is only available while the printer is idle. Current state: {label}.")
    return idle


def _extract_filament_info(snapshot: dict[str, Any] | None, command_result: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    raw_status = snapshot.get("raw_status") or {}
    normalized = snapshot.get("normalized") or {}
    roots = [command_result, raw_status.get("canvas"), raw_status.get("canvas_info"), raw_status, snapshot]
    root = None
    for candidate in roots:
        root = _find_filament_root(candidate)
        if root:
            break

    mms_list_raw = []
    system_name = "CANVAS"
    connected = None
    auto_refill = None
    if root:
        system_name = str(_dig(root, "mmsSystemName", "mms_system_name", "systemName", "system_name", "name", default="CANVAS") or "CANVAS")
        connected = _boolish(_dig(root, "connected", "isConnected", "mmsConnected", default=None))
        auto_refill = _boolish(_dig(root, "autoRefill", "auto_refill", "autoRefillEnabled", "auto_refill_enabled", "autoFill", "auto_fill", "autoFillFilament", "auto_fill_filament", default=None))
        mms_list_raw = _as_list(_dig(root, "mmsList", "mms_list", "MmsList", "canvasList", "canvas_list", "CanvasList", default=[]))
        if not mms_list_raw:
            trays = _as_list(_dig(root, "trayList", "tray_list", "TrayList", default=[]))
            if trays:
                mms_list_raw = [{"mmsId": "canvas-1", "mmsName": system_name, "trayList": trays}]

    mms_list = []
    trays_flat = []
    for mms_index, mms in enumerate(mms_list_raw):
        if not isinstance(mms, dict):
            continue
        mms_id = str(_dig(mms, "mmsId", "mms_id", "canvasId", "canvas_id", "id", default=f"{mms_index}") or f"{mms_index}")
        tray_list = _as_list(_dig(mms, "trayList", "tray_list", "TrayList", default=[]))
        trays = [_normalize_tray(t, mms_id=mms_id, index=i) for i, t in enumerate(tray_list) if isinstance(t, dict)]
        trays_flat.extend(trays)
        mms_list.append({
            "mms_id": mms_id,
            "mms_name": str(_dig(mms, "mmsName", "mms_name", "canvasName", "canvas_name", "name", default=f"CANVAS {mms_index + 1}") or f"CANVAS {mms_index + 1}"),
            "connected": _boolish(_dig(mms, "connected", "isConnected", "is_connected", default=connected)),
            "tray_count": len(trays),
            "active_count": sum(1 for t in trays if t.get("active")),
            "trays": trays,
            "raw": mms,
        })

    sensor = (normalized.get("filament") or {}) if isinstance(normalized, dict) else {}
    idle_state = _filament_idle_state(snapshot)
    sensor_enabled = _boolish(sensor.get("sensor_enabled"))
    sensor_detected = _boolish(sensor.get("detected"))
    # Firmware builds expose the runout sensor through a few different status
    # paths. The normalized telemetry path is preferred, then we cautiously
    # scan the raw status blob for stock-style field names before giving up.
    if sensor_enabled is None:
        sensor_enabled = _boolish(_find_first_key(
            raw_status,
            "filament_detect_enable", "filament_detect_enabled",
            "filamentDetectEnable", "filamentDetectEnabled",
            "filament_sensor_enable", "filamentSensorEnable",
            "filament_sensor_enabled", "filamentSensorEnabled",
            "runoutSensorEnabled", "filamentRunoutSensorEnabled",
            max_depth=4,
        ))
    if sensor_detected is None:
        sensor_detected = _boolish(_find_first_key(
            raw_status,
            "filament_detected", "filament_detect",
            "filamentDetected", "filamentDetect",
            "filament_sensor_detected", "filamentSensorDetected",
            "filament_sensor_status", "filamentSensorStatus",
            "has_filament", "hasFilament",
            "filament_state", "filamentState",
            "runoutStatus",
            max_depth=4,
        ))
    return {
        "ok": True,
        "printer": {
            "id": snapshot.get("id"),
            "name": snapshot.get("name"),
            "host": snapshot.get("host"),
            "connected": snapshot.get("connected"),
            "registered": snapshot.get("registered"),
        },
        "system_name": system_name,
        "connected": connected if connected is not None else bool(mms_list),
        "auto_refill": auto_refill,
        "mms_list": mms_list,
        "trays": trays_flat,
        "tray_count": len(trays_flat),
        "active_count": sum(1 for t in trays_flat if t.get("active")),
        "sensor": {
            "enabled": sensor_enabled,
            "detected": sensor_detected,
        },
        "active_print": idle_state.get("active_print"),
        "printer_idle": idle_state.get("printer_idle"),
        "printer_state": idle_state.get("state"),
        "printer_sub_state": idle_state.get("sub_state"),
        "source": "canvas_status" if root else "telemetry_only",
        "raw_available": bool(root),
        "raw": root or {},
    }


def _speed_label(mode: Any, raw_speed: Any = None, speed_percent: Any = None) -> str:
    try:
        value = int(float(mode))
        if value in SPEED_PRESETS:
            return SPEED_PRESETS[value]
    except Exception:
        pass
    if isinstance(mode, str) and mode.strip():
        lowered = mode.strip().lower()
        aliases = {"silent": "Silent", "slient": "Silent", "balanced": "Balanced", "sport": "Sport", "ludicrous": "Ludicrous", "frenzy": "Ludicrous"}
        if lowered in aliases:
            return aliases[lowered]
    if speed_percent not in (None, ""):
        try:
            return f"{float(speed_percent):.0f}%"
        except Exception:
            return str(speed_percent)
    if raw_speed not in (None, ""):
        try:
            value = float(raw_speed)
            # Some Elegoo payloads expose print speed override as 50/100/125/etc.
            # Others expose movement feedrate. Avoid pretending a percent is mm/s.
            if 0 <= value <= 300:
                return f"{value:.0f}%"
            return f"{value:.0f} mm/s"
        except Exception:
            return str(raw_speed)
    return "-"


def _get_nested(data: Any, path: str, default: Any = None) -> Any:
    cur = data
    for part in str(path or "").split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _first_status_value(data: Any, paths: list[str]) -> tuple[Any, str | None]:
    for path in paths:
        value = _get_nested(data, path)
        if value not in (None, "", "-"):
            return value, path
    # Last resort: search case-ish key names through the raw blob.
    keys = [p.split(".")[-1] for p in paths]
    found = _find_first_key(data, *keys, max_depth=6)
    if found not in (None, "", "-"):
        return found, "recursive:" + "/".join(keys[:3])
    return None, None


def _format_layer_progress(current: Any, total: Any) -> str:
    def to_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            number = int(float(value))
            return number if number >= 0 else None
        except Exception:
            return None
    cur = to_int(current)
    tot = to_int(total)
    if cur is not None and tot is not None and tot > 0:
        return f"{cur}/{tot}"
    if cur is not None and cur > 0:
        return str(cur)
    if tot is not None and tot > 0:
        return f"-/{tot}"
    return "-"


def _format_filament_used(value: Any, source: str | None = None) -> str:
    if value in (None, "", "-"):
        return "-"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "-"
        # Preserve values that already include a unit from firmware.
        lowered = text.lower()
        if any(unit in lowered for unit in (" g", "kg", " mm", "cm", " m")):
            return text
        raw = text.replace(",", "")
    else:
        raw = value
    try:
        number = float(raw)
    except Exception:
        return str(value)
    if number <= 0:
        return "-"
    key = str(source or "").lower()
    if "kg" in key:
        return f"{number:.3g} kg"
    if "meter" in key or key.endswith("_m") or key.endswith(".m"):
        return f"{number:.2f} m"
    if "length" in key or key.endswith("_mm") or "filamentlen" in key:
        return f"{number / 1000.0:.2f} m" if number >= 1000 else f"{number:.0f} mm"
    # Stock portal file-detail data maps totalFilamentUsed to materialWeight and displays grams.
    return f"{number:.1f} g" if number < 100 else f"{number:.0f} g"


def _extract_print_metrics(snap: dict[str, Any] | None, normalized: dict[str, Any]) -> dict[str, Any]:
    snap = snap or {}
    raw_status = snap.get("raw_status") or {}
    layers = normalized.get("layers") or {}
    current_layer, current_src = _first_status_value(raw_status, [
        "print_status.current_layer", "print_status.currentLayer", "print_status.currentLayerIndex",
        "print_status.CurrentLayer", "print_status.AlreadyPrintLayer", "PrintInfo.CurrentLayer",
        "printInfo.currentLayer", "current_layer", "currentLayer", "CurrentLayer", "AlreadyPrintLayer",
    ])
    total_layer, total_src = _first_status_value(raw_status, [
        "print_status.total_layer", "print_status.totalLayer", "print_status.totalLayers",
        "print_status.TotalLayer", "print_status.TotalLayers", "PrintInfo.TotalLayer",
        "printInfo.totalLayer", "total_layer", "totalLayer", "totalLayers", "TotalLayer", "TotalLayers",
    ])
    current_layer = layers.get("current") if layers.get("current") not in (None, "") else current_layer
    total_layer = layers.get("total") if layers.get("total") not in (None, "") else total_layer

    filament_used, filament_src = _first_status_value(raw_status, [
        "print_status.filament_used", "print_status.filamentUsed", "print_status.FilamentUsed",
        "print_status.total_filament_used", "print_status.totalFilamentUsed", "print_status.TotalFilamentUsed",
        "print_status.material_weight", "print_status.materialWeight", "print_status.MaterialWeight",
        "print_status.filament_weight", "print_status.filamentWeight", "print_status.FilamentWeight",
        "print_status.filament_length", "print_status.filamentLength", "print_status.FilamentLength",
        "PrintInfo.FilamentUsed", "PrintInfo.TotalFilamentUsed", "PrintInfo.MaterialWeight", "PrintInfo.FilamentLength",
        "printInfo.filamentUsed", "printInfo.totalFilamentUsed", "printInfo.materialWeight", "printInfo.filamentLength",
        "filament_used", "filamentUsed", "FilamentUsed", "total_filament_used", "totalFilamentUsed", "TotalFilamentUsed",
        "material_weight", "materialWeight", "MaterialWeight", "filament_length", "filamentLength", "FilamentLength",
    ])
    return {
        "layer_current": current_layer,
        "layer_total": total_layer,
        "layer_progress": _format_layer_progress(current_layer, total_layer),
        "layer_source": {"current": current_src, "total": total_src},
        "filament_used": _format_filament_used(filament_used, filament_src),
        "filament_used_raw": filament_used,
        "filament_used_source": filament_src,
    }


def _looks_like_image_bytes(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _iter_thumbnail_candidates(node: Any):
    preferred = {
        "thumbnail", "Thumbnail", "thumb", "Thumb", "image", "Image", "data", "Data",
        "base64", "Base64", "file_thumbnail", "fileThumbnail", "FileThumbnail",
        "preview", "Preview", "previewImage", "PreviewImage", "model_image", "modelImage",
    }
    if isinstance(node, dict):
        for key in preferred:
            if key in node:
                yield node[key]
        for value in node.values():
            yield from _iter_thumbnail_candidates(value)
    elif isinstance(node, list):
        for value in node[:12]:
            yield from _iter_thumbnail_candidates(value)
    elif isinstance(node, str):
        yield node


def _extract_thumbnail_image(payload: Any) -> tuple[bytes | None, str | None, str | None]:
    root = payload
    if isinstance(root, dict) and "result" in root:
        root = root.get("result")
    if isinstance(root, dict) and "data" in root and len(root) <= 4:
        # Some firmware wrappers use {error_code, data:{thumbnail:...}}.
        root = root.get("data") or root
    for candidate in _iter_thumbnail_candidates(root):
        if candidate in (None, ""):
            continue
        if isinstance(candidate, (bytes, bytearray)):
            data = bytes(candidate)
            media = _looks_like_image_bytes(data)
            if media:
                return data, media, None
            continue
        if isinstance(candidate, list) and candidate and all(isinstance(x, int) for x in candidate[:16]):
            try:
                data = bytes(candidate)
                media = _looks_like_image_bytes(data)
                if media:
                    return data, media, None
            except Exception:
                pass
        if not isinstance(candidate, str):
            continue
        text = candidate.strip().strip('"')
        if not text:
            continue
        if text.startswith("http://") or text.startswith("https://") or text.startswith("/"):
            return None, None, text
        if text.startswith("data:image/"):
            try:
                header, encoded = text.split(",", 1)
                media = header.split(";", 1)[0].replace("data:", "") or "image/png"
                return base64.b64decode(encoded), media, None
            except Exception:
                continue
        compact = "".join(text.split())
        if len(compact) < 80:
            continue
        try:
            data = base64.b64decode(compact, validate=False)
        except Exception:
            continue
        media = _looks_like_image_bytes(data)
        if media:
            return data, media, None
    return None, None, None


class ScanRequest(BaseModel):
    subnet: str | None = None
    ports: list[int] | None = None


class AddPrinterRequest(BaseModel):
    id: str | None = None
    name: str = "Centauri Carbon 2"
    host: str
    serial: str | None = None
    access_code: str = ""
    port: int = 1883
    enabled: bool = True
    allow_commands: bool = True
    allow_dangerous_commands: bool = False
    portal_url: str | None = None
    camera_url: str | None = None
    set_default: bool = True


class PrinterSettingsRequest(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    serial: Optional[str] = None
    access_code: Optional[str] = None
    port: Optional[int] = None
    enabled: Optional[bool] = None
    allow_commands: Optional[bool] = None
    allow_dangerous_commands: Optional[bool] = None


class ActionRequest(BaseModel):
    printer_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class CommandRequest(BaseModel):
    method: int
    params: dict[str, Any] = Field(default_factory=dict)
    wait: bool = True
    timeout: float = 10.0


class LightRequest(BaseModel):
    on: bool


class FilamentAutoRefillRequest(BaseModel):
    enabled: bool


class FilamentMotionRequest(BaseModel):
    canvas_id: int | str = 0
    tray_id: int | str


class FilamentInfoRequest(BaseModel):
    canvas_id: int | str = 0
    tray_id: int | str
    brand: str = "ELEGOO"
    filament_type: str = "PLA"
    filament_name: str = "PLA"
    filament_code: str = ""
    filament_color: str = "#8b8f9a"
    filament_min_temp: int = 190
    filament_max_temp: int = 230


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    # Pydantic v1/v2 compatibility. Raspberry Pi installs tend to have both in
    # the wild, and this app shouldn't care which one won the dependency lottery.
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class DeleteFileRequest(BaseModel):
    file_path: str
    storage_media: str = "local"


class StartPrintRequest(BaseModel):
    filename: str
    storage_media: str = "local"
    start_layer: int = 0
    calibration: bool = False
    platform_type: int = 0
    timelapse: bool = False


class TimelapseExportRequest(BaseModel):
    url: str


class HistoryDeleteRequest(BaseModel):
    task_ids: list[str | int]


class SaveConfigRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class AIFeedbackRequest(BaseModel):
    label: str
    note: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class LearningResetRequest(BaseModel):
    delete_samples: bool = False


class OllamaPullRequest(BaseModel):
    model: str
    base_url: Optional[str] = None


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def _allowed_request(request: Request, cfg: dict) -> bool:
    ip = _client_ip(request)
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip in {"testclient"}
    net_cfg = cfg.get("network", {})
    if net_cfg.get("always_allow_localhost", True) and addr.is_loopback:
        return True
    for host in net_cfg.get("allowed_hosts", []) or []:
        try:
            if addr == ipaddress.ip_address(host):
                return True
        except ValueError:
            continue
    for subnet in net_cfg.get("allowed_subnets", []) or []:
        try:
            if addr in ipaddress.ip_network(subnet, strict=False):
                return True
        except ValueError:
            continue
    return False


@app.middleware("http")
async def lan_guard(request: Request, call_next):
    cfg = load_config()
    if not _allowed_request(request, cfg):
        return JSONResponse({"ok": False, "error": "Client IP is not allowed by cc2-dash network settings."}, status_code=403)
    return await call_next(request)


def _level_rank(level: str | None) -> int:
    ranks = {"disabled": 0, "low": 10, "watch": 25, "medium": 50, "high": 75}
    return ranks.get(str(level or "low").lower(), 0)


def _start_ai_monitor() -> None:
    global _AI_MONITOR_TASK
    if _AI_MONITOR_TASK and not _AI_MONITOR_TASK.done():
        return
    _AI_MONITOR_TASK = asyncio.create_task(_ai_monitor_loop())
    log("info", "Portal AI background watchdog task started", "portal_ai")


def _should_log_ai_change(printer_id: str, result: dict[str, Any], ai_cfg: dict[str, Any]) -> bool:
    min_level = str(ai_cfg.get("background_min_log_level", "watch") or "watch").lower()
    if _level_rank(result.get("level")) < _level_rank(min_level):
        return False
    previous = _AI_MONITOR_LAST_LOGGED.get(printer_id) or {}
    if not previous:
        return True
    if not ai_cfg.get("background_log_changes", True):
        return True
    old_level = previous.get("level")
    old_state = previous.get("state")
    old_risk = int(previous.get("risk") or 0)
    risk = int(result.get("risk") or 0)
    return old_level != result.get("level") or old_state != result.get("state") or abs(risk - old_risk) >= 10


async def _ai_monitor_loop() -> None:
    """Background Portal AI watchdog.

    This keeps the rule engine evaluating even when no browser is open. The
    dashboard can then simply display the latest cached result, while this loop
    handles state/risk changes and logging in the running backend service.
    """
    await asyncio.sleep(2)
    _AI_MONITOR_STATE["running"] = True
    while True:
        try:
            cfg = load_config()
            ai_cfg = cfg.get("portal_ai", {}) or {}
            interval = max(5.0, min(600.0, float(ai_cfg.get("check_interval_seconds") or 30)))
            if ai_cfg.get("enabled", True) and ai_cfg.get("background_monitor_enabled", True):
                printers = cfg.get("printers") or {}
                for printer_id, printer in list(printers.items()):
                    if not (printer or {}).get("enabled", True):
                        continue
                    if not runtime.get_client(printer_id):
                        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
                    snap = runtime.snapshot(printer_id)
                    status = await asyncio.to_thread(
                        _status_from_snapshot,
                        printer_id,
                        printer,
                        snap,
                        "background",
                        True,
                    )
                    result = status.get("portal_ai") or {}
                    if result:
                        if _should_log_ai_change(printer_id, result, ai_cfg):
                            risk = int(result.get("risk") or 0)
                            level = str(result.get("level") or "low").upper()
                            reason = (result.get("reasons") or ["No reason returned."])[0]
                            log_level = "warning" if risk >= 50 else "info"
                            log(log_level, f"AI watchdog {level} {risk}%: {reason}", "portal_ai", printer=printer_id)
                            _AI_MONITOR_LAST_LOGGED[printer_id] = {
                                "risk": risk,
                                "level": result.get("level"),
                                "state": result.get("state"),
                                "ts": time.time(),
                            }
                _AI_MONITOR_STATE["iterations"] = int(_AI_MONITOR_STATE.get("iterations") or 0) + 1
                _AI_MONITOR_STATE["last_loop_epoch"] = time.time()
                _AI_MONITOR_STATE["last_loop"] = time.strftime("%H:%M:%S")
                _AI_MONITOR_STATE["last_error"] = None
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            _AI_MONITOR_STATE["running"] = False
            raise
        except Exception as exc:
            _AI_MONITOR_STATE["last_error"] = str(exc)
            log("error", f"Portal AI watchdog error: {exc}", "portal_ai")
            await asyncio.sleep(15)


@app.on_event("startup")
async def startup_event() -> None:
    try:
        ai_learning.ensure_database()
    except Exception as exc:
        log("warning", f"AI learning database initialization failed: {exc}", "portal_ai")
    runtime.start_all()
    camera_relays.configure_from_config(load_config())
    _start_ai_monitor()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _AI_MONITOR_TASK
    if _AI_MONITOR_TASK:
        _AI_MONITOR_TASK.cancel()
        try:
            await _AI_MONITOR_TASK
        except asyncio.CancelledError:
            pass
        _AI_MONITOR_TASK = None
    camera_relays.stop_all()
    runtime.stop_all()


def _configured_printers() -> dict[str, dict[str, Any]]:
    return load_config().get("printers", {}) or {}


def _portal_target(printer: Optional[str] = None):
    cfg = load_config()
    printers = cfg.get("printers") or {}
    if printer:
        if printer in printers:
            return printer_dict_to_config(printer, printers[printer])
        lowered = printer.lower()
        for pid, pdata in printers.items():
            pcfg = printer_dict_to_config(pid, pdata)
            if pcfg.host == printer or pcfg.name.lower() == lowered or pcfg.serial.lower() == lowered:
                return pcfg
    pid, pdata = default_printer(cfg)
    if pid and pdata:
        return printer_dict_to_config(pid, pdata)
    return None


def view_context(request: Request) -> dict[str, Any]:
    cfg = load_config()
    theme = get_theme(cfg.get("app", {}).get("theme"))
    pid, printer = default_printer(cfg)
    public_printer = None
    if pid and printer:
        public_printer = public_printer_dict(printer_dict_to_config(pid, printer), include_secret=False)
    return {
        "request": request,
        "version": __version__,
        "build": get_build_info(),
        "cfg": cfg,
        "needs_setup": needs_setup(cfg),
        "cards": sorted_cards(cfg),
        "actions": sorted_actions(cfg),
        "themes": THEMES,
        "font_stacks": FONT_STACKS,
        "theme": theme,
        "theme_vars": theme_css_vars(cfg.get("app", {}).get("theme"), cfg.get("appearance", {})),
        "printer_id": pid,
        "printer": public_printer,
        "default_subnet": default_subnet_guess(),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("index.html", view_context(request))


@app.get("/setup", response_class=HTMLResponse)
async def setup(request: Request):
    return templates.TemplateResponse("setup.html", view_context(request))


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return templates.TemplateResponse("settings.html", view_context(request))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return templates.TemplateResponse("logs.html", view_context(request))


@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("files.html", view_context(request))


@app.get("/filaments", response_class=HTMLResponse)
async def filaments_page(request: Request):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("filaments.html", view_context(request))




@app.get("/kiosk", response_class=HTMLResponse)
async def kiosk(request: Request, printer: Optional[str] = None):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    pcfg = _portal_target(printer)
    if not pcfg:
        return RedirectResponse("/setup")
    context = view_context(request)
    context["printer_id"] = pcfg.id
    context["printer"] = public_printer_dict(pcfg, include_secret=False)
    return templates.TemplateResponse("kiosk.html", context)

@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request, printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        return RedirectResponse("/setup")
    root_url = f"http://{pcfg.host}/"
    octo_url = f"/portal-octo?printer={pcfg.id}"
    fullscreen_url = f"/portal-fullscreen?printer={pcfg.id}"
    diag_url = f"/api/portal-probe?printer={pcfg.id}"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Elegoo Portal - cc2-dash</title>
  <style>
    html,body{{margin:0;height:100%;background:#111827;color:#e5e7eb;font-family:system-ui,sans-serif;}}
    .bar{{min-height:46px;display:flex;gap:12px;align-items:center;padding:0 14px;background:rgba(17,24,39,.94);border-bottom:1px solid rgba(148,163,184,.18);backdrop-filter:blur(12px);flex-wrap:wrap}}
    .bar strong{{font-size:14px;white-space:nowrap}} .bar span{{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    .bar a{{color:#93c5fd;text-decoration:none;font-size:13px;white-space:nowrap}}
    iframe{{display:block;width:100%;height:calc(100vh - 47px);border:0;background:#202124;}}
  </style>
</head>
<body>
  <div class="bar"><strong>Elegoo portal</strong><span>{pcfg.name} · {pcfg.host}</span><a href="/">Back</a><a href="{fullscreen_url}" target="_blank">Fullscreen</a><a href="{root_url}" target="_blank">Printer root</a><a href="{diag_url}" target="_blank">Probe</a></div>
  <iframe src="{octo_url}" title="Elegoo live portal"></iframe>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/portal-octo", response_class=HTMLResponse)
async def portal_octo(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        return HTMLResponse("""<!doctype html><html><body style="background:#111827;color:#e5e7eb;font-family:system-ui;padding:32px"><h1>No printer configured</h1><p>Add/scan your printer first.</p><p><a style="color:#93c5fd" href="/setup">Back to setup</a></p></body></html>""")
    app_url = (
        f"/elegoo/octo_portal.html"
        f"?id={pcfg.id}&ip={pcfg.host}&print_ip={pcfg.host}&sn={pcfg.serial}"
        f"&access_code={pcfg.access_code}&username=elegoo&lang=en-US#/index"
    )
    html = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>Elegoo Live Portal - cc2-dash</title>
<style>html,body{{margin:0;height:100%;background:#111827;color:#e5e7eb;font-family:system-ui,sans-serif;}}.bar{{min-height:46px;display:flex;gap:12px;align-items:center;padding:0 14px;background:rgba(17,24,39,.92);border-bottom:1px solid rgba(148,163,184,.18);backdrop-filter:blur(12px);flex-wrap:wrap}}.bar strong{{font-size:14px;white-space:nowrap}}.bar span{{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar a{{color:#93c5fd;text-decoration:none;font-size:13px;white-space:nowrap}}iframe{{display:block;width:100%;height:calc(100vh - 47px);border:0;background:#202124;}}</style>
</head><body><div class="bar"><strong>Elegoo live portal</strong><span>{pcfg.name} · MQTT WS bridge · {pcfg.host}:{pcfg.port}</span><a href="/">Back</a><a href="{app_url}" target="_blank">Open raw app</a><a href="/api/portal-probe?printer={pcfg.id}" target="_blank">Probe</a></div><iframe src="{app_url}" title="Elegoo live portal"></iframe></body></html>"""
    return HTMLResponse(html)


@app.get("/portal-fullscreen", response_class=HTMLResponse)
async def portal_fullscreen(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        return HTMLResponse("""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CC2 setup</title></head><body style="margin:0;background:#05070b;color:#e5e7eb;font-family:system-ui;display:grid;place-items:center;min-height:100vh;padding:20px;text-align:center"><div><h1>No printer configured</h1><p>Run setup first.</p><p><a style="color:#7dd3fc" href="/setup">Open setup</a></p></div></body></html>""")
    app_url = (
        f"/elegoo/octo_portal.html"
        f"?id={pcfg.id}&ip={pcfg.host}&print_ip={pcfg.host}&sn={pcfg.serial}"
        f"&access_code={pcfg.access_code}&username=elegoo&lang=en-US#/index"
    )
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<meta name="theme-color" content="#202124" />
<title>Elegoo Portal</title>
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#202124}}iframe{{display:block;width:100vw;height:100dvh;border:0;background:#202124}}</style>
</head><body><iframe src="{app_url}" title="Elegoo Portal"></iframe></body></html>""")


@app.get("/oe-relay-static/elegoo-os-relay.js")
async def oe_relay_js():
    return HTMLResponse("console.log('[cc2-dash] local oe relay stub loaded');", media_type="application/javascript")


@app.get("/oe-relay-static/elegoo-os-relay.css")
async def oe_relay_css():
    return HTMLResponse("/* cc2-dash local oe relay css stub */", media_type="text/css")


@app.websocket("/ws/mqtt/{printer_id}")
async def mqtt_websocket_bridge(websocket: WebSocket, printer_id: str) -> None:
    pcfg = _portal_target(printer_id)
    if not pcfg:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=websocket.headers.get("sec-websocket-protocol"))
    reader = writer = None
    try:
        reader, writer = await asyncio.open_connection(pcfg.host, pcfg.port)

        async def ws_to_tcp() -> None:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is None:
                    text = msg.get("text")
                    if text is None:
                        continue
                    data = text.encode("utf-8")
                writer.write(data)
                await writer.drain()

        async def tcp_to_ws() -> None:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)

        _, pending = await asyncio.wait(
            {asyncio.create_task(ws_to_tcp()), asyncio.create_task(tcp_to_ws())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log("warn", f"MQTT WS bridge failed for {printer_id}: {exc}", "portal")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


@app.get("/health")
async def health():
    cfg = load_config()
    return {
        "ok": True,
        "version": __version__,
        "build": get_build_info(),
        "setup_required": needs_setup(cfg),
        "printers": len(cfg.get("printers") or {}),
        "camera_relays": camera_relays.status_all(),
        "ai_learning": ai_learning.db_health(),
    }




@app.get("/api/version")
async def api_version():
    return {"ok": True, "build": get_build_info()}

@app.get("/api/health")
async def api_health():
    return await health()


@app.get("/api/config")
async def api_get_config():
    return {"ok": True, "config": load_config(), "themes": THEMES, "font_stacks": list(FONT_STACKS.keys())}


@app.post("/api/config")
async def api_save_config(req: SaveConfigRequest):
    cfg = save_config(req.config)
    runtime.reload()
    camera_relays.configure_from_config(cfg)
    log("info", "Configuration saved", "settings")
    return {"ok": True, "config": cfg}


def _discovery_targets(subnet_or_host: str) -> list[str]:
    subnet_or_host = (subnet_or_host or default_subnet_guess()).strip()
    targets: list[str] = []
    try:
        if "/" in subnet_or_host:
            net = ipaddress.ip_network(subnet_or_host, strict=False)
            targets.append(str(net.broadcast_address))
        elif subnet_or_host.endswith(".x"):
            targets.append(subnet_or_host[:-2] + ".255")
        else:
            ipaddress.ip_address(subnet_or_host)
            targets.append(subnet_or_host)
    except Exception:
        pass
    targets.append("255.255.255.255")
    out = []
    for t in targets:
        if t not in out:
            out.append(t)
    return out


def _cc2_discovery_row(d: dict[str, Any], host: str | None = None, notes: list[str] | None = None) -> dict[str, Any] | None:
    """Normalize a UDP method-7000 response into a UI scan row.

    This is the proof-of-printer path. Generic HTTP/TCP results are not shown
    unless a directed UDP CC2 probe verifies them first. That keeps routers,
    Tasmota plugs, phones, and random web UIs from showing up as pairable
    printer candidates just because port 80 answered.
    """
    host = host or d.get("ip")
    if not host:
        return None
    serial = str(d.get("serial") or d.get("sn") or "").strip()
    model = str(d.get("machine_model") or d.get("model") or "Centauri Carbon 2").strip()
    host_name = str(d.get("host_name") or d.get("hostname") or "Centauri Carbon 2").strip()
    proof = []
    if serial:
        proof.append(f"serial {serial}")
    if model:
        proof.append(model)
    if d.get("raw"):
        proof.append("method 7000 response")
    row_notes = list(notes or [])
    row_notes.append("Verified by Centauri UDP discovery method 7000")
    return {
        "host": host,
        "open_ports": [1883, 80, 8080],
        "http_title": model or host_name or "Centauri Carbon 2",
        "likely_printer": True,
        "verified_printer": True,
        "verified_by": "udp_method_7000",
        "verification_proof": proof,
        "notes": row_notes,
        "portal_url": f"http://{host}/",
        "camera_url": f"http://{host}:8080/",
        "serial": serial,
        "host_name": host_name or "Centauri Carbon 2",
        "machine_model": model or "Centauri Carbon 2",
        "token_status": d.get("token_status"),
        "lan_status": d.get("lan_status"),
        "raw": d.get("raw"),
    }


async def _discover_cc2(subnet_or_host: str, timeout: float = 3.5) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for target in _discovery_targets(subnet_or_host):
        try:
            rows = await asyncio.to_thread(discover, timeout, target)
            for p in rows:
                d = p.to_dict()
                row = _cc2_discovery_row(d, notes=[f"UDP target {target}"])
                if row:
                    found[row["host"]] = row
        except Exception as exc:
            log("warn", f"UDP discovery failed for target {target}: {exc}", "scanner")
    return list(found.values())


def _generic_candidate_is_worth_verifying(candidate: dict[str, Any]) -> bool:
    ports = set(int(p) for p in (candidate.get("open_ports") or []) if str(p).isdigit())
    text = " ".join([
        str(candidate.get("http_title") or ""),
        " ".join(str(n) for n in (candidate.get("notes") or [])),
    ]).lower()
    if any(word in text for word in ["elegoo", "centauri"]):
        return True
    # CC2 local control/webcam stack typically exposes MQTT plus web/camera.
    return 1883 in ports and bool(ports.intersection({80, 8080}))


async def _direct_verify_cc2(host: str, timeout: float = 1.2) -> dict[str, Any] | None:
    try:
        rows = await asyncio.to_thread(discover, timeout, host)
    except Exception as exc:
        log("debug", f"Directed CC2 verify failed for {host}: {exc}", "scanner")
        return None
    for p in rows:
        d = p.to_dict()
        # Some devices answer from their own source IP even when the target is a
        # directed unicast. Accept either the requested host or the reported IP.
        reported = d.get("ip") or host
        if reported and str(reported) not in {str(host), "0.0.0.0"}:
            continue
        row = _cc2_discovery_row(d, host=host, notes=["Directed verification after TCP scan"])
        if row:
            return row
    return None


@app.get("/api/discover")
async def api_discover(timeout: float = Query(4.0, ge=0.5, le=15.0), target: str = Query("255.255.255.255")):
    printers = await asyncio.to_thread(discover, timeout, target)
    return {"count": len(printers), "printers": [p.to_dict() for p in printers]}


@app.post("/api/scan")
async def api_scan(req: ScanRequest):
    cfg = load_config()
    subnet = req.subnet or (cfg.get("network", {}).get("allowed_subnets") or [default_subnet_guess()])[0]
    ports = req.ports or cfg.get("network", {}).get("scan_ports") or [80, 8080, 3030, 1883, 8899]
    try:
        udp_found = await _discover_cc2(subnet)
        generic_found = await scan_network(subnet, ports)
    except Exception as exc:
        log("error", f"Scan failed: {exc}", "scanner")
        raise HTTPException(status_code=400, detail=str(exc))

    verified: dict[str, dict[str, Any]] = {c["host"]: c for c in udp_found if c.get("host")}
    rejected: list[dict[str, Any]] = []

    # Generic TCP/HTTP scan results are now treated as hints only. They must pass
    # a directed CC2 method-7000 verification before the UI offers Pair/Save.
    verify_tasks: list[tuple[dict[str, Any], asyncio.Task]] = []
    for c in generic_found:
        host = c.get("host")
        if not host or host in verified:
            continue
        if _generic_candidate_is_worth_verifying(c):
            verify_tasks.append((c, asyncio.create_task(_direct_verify_cc2(host))))
        else:
            c["reject_reason"] = "No Centauri discovery response/proof; generic network device hidden."
            rejected.append(c)

    for original, task in verify_tasks:
        row = await task
        if row:
            # Preserve the actual TCP port list from the generic scan when present.
            if original.get("open_ports"):
                row["open_ports"] = original.get("open_ports")
            verified[row["host"]] = row
        else:
            original["reject_reason"] = "TCP ports looked possible, but directed Centauri discovery did not verify it."
            rejected.append(original)

    candidates = sorted(verified.values(), key=lambda c: c.get("host", ""))
    log("info", f"Scan complete: {len(candidates)} verified printer(s), {len(rejected)} hidden non-printer candidate(s)", "scanner")
    return {
        "ok": True,
        "subnet": subnet,
        "ports": ports,
        "candidates": candidates,
        "verified_count": len(candidates),
        "hidden_count": len(rejected),
    }


@app.get("/api/printers")
async def api_list_printers():
    cfg = load_config()
    configured = []
    for printer_id, data in (cfg.get("printers") or {}).items():
        configured.append(public_printer_dict(printer_dict_to_config(printer_id, data), include_secret=False))
    return {"configured": configured, "status": runtime.snapshots()}


@app.post("/api/printers")
async def api_add_printer(req: AddPrinterRequest):
    cfg = load_config()
    access_code = (req.access_code or "").strip()
    if not access_code:
        raise HTTPException(status_code=400, detail="Printer PIN / access code is required")
    serial = (req.serial or "").strip() or req.host.strip()
    safe_id = req.id or safe_printer_id(serial or req.name or req.host)
    base_id = safe_id
    n = 2
    while safe_id in cfg.get("printers", {}) and not req.id:
        safe_id = f"{base_id}-{n}"
        n += 1
    cfg.setdefault("printers", {})[safe_id] = {
        "name": req.name,
        "host": req.host.strip(),
        "serial": serial,
        "access_code": access_code,
        "port": int(req.port or 1883),
        "model": "centauri_carbon_2",
        "enabled": bool(req.enabled),
        "paired": True,
        "allow_commands": bool(req.allow_commands),
        "allow_dangerous_commands": bool(req.allow_dangerous_commands),
        "portal_enabled": True,
        "camera_enabled": True,
        "portal_url": f"/portal-fullscreen?printer={safe_id}",
        "direct_portal_url": req.portal_url or f"http://{req.host}/",
        "camera_url": f"/api/printers/{safe_id}/camera/stream",
        "direct_camera_url": req.camera_url or f"http://{req.host}:8080/",
    }
    if req.set_default or not cfg.get("app", {}).get("default_printer"):
        cfg.setdefault("app", {})["default_printer"] = safe_id
    cfg.setdefault("app", {})["setup_complete"] = True
    cfg = save_config(cfg)
    runtime.restart(safe_id, printer_dict_to_config(safe_id, cfg["printers"][safe_id]))
    log("info", f"Printer paired/saved: {req.name} at {req.host} serial={serial}", "setup")
    return {"ok": True, "printer_id": safe_id, "config": cfg, "printer": public_printer_dict(printer_dict_to_config(safe_id, cfg["printers"][safe_id]))}


@app.patch("/api/printers/{printer_id}")
async def api_update_printer(printer_id: str, patch: PrinterSettingsRequest):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    data = cfg["printers"][printer_id]
    for key, value in patch.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key == "access_code" and value == "":
            continue
        data[key] = value
    cfg = save_config(cfg)
    runtime.restart(printer_id, printer_dict_to_config(printer_id, cfg["printers"][printer_id]))
    return {"ok": True, "config": cfg, "printer": public_printer_dict(printer_dict_to_config(printer_id, cfg["printers"][printer_id]))}


@app.delete("/api/printers/{printer_id}")
async def api_delete_printer(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    runtime.stop(printer_id)
    cfg["printers"].pop(printer_id, None)
    if cfg.get("app", {}).get("default_printer") == printer_id:
        cfg["app"]["default_printer"] = next(iter(cfg.get("printers", {}).keys()), None)
    if not cfg.get("printers"):
        cfg.setdefault("app", {})["setup_complete"] = False
    cfg = save_config(cfg)
    return {"ok": True, "config": cfg}


@app.post("/api/printers/{printer_id}/default")
async def api_set_default_printer(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    cfg.setdefault("app", {})["default_printer"] = printer_id
    cfg.setdefault("app", {})["setup_complete"] = True
    cfg = save_config(cfg)
    log("info", f"Default printer set to {printer_id}", "settings")
    return {"ok": True, "config": cfg, "printer_id": printer_id}


def _maybe_attach_vision(printer_id: str, printer: dict[str, Any] | None, status: dict[str, Any], cfg: dict[str, Any], ai_source: str = "request", force: bool = False) -> dict[str, Any]:
    ai_cfg = cfg.get("portal_ai", {}) or {}
    if ai_cfg.get("monitor_active_prints_only", True) and not bool(status.get("active_print")):
        if ai_cfg.get("vision_ai_enabled", False):
            status["vision_ai"] = _idle_vision_result(printer_id, ai_source)
        return status
    if not ai_cfg.get("vision_ai_enabled", False):
        cached = vision_monitor.cached_result(printer_id)
        if cached:
            status["vision_ai"] = cached
        return status

    # Normal browser refreshes should display the background watchdog cache. The
    # backend watchdog and explicit Check Now calls are allowed to run the camera +
    # Ollama path. That keeps the UI snappy and stops every refresh from poking
    # the model like it owes us money.
    should_run = force or ai_source == "background"
    try:
        if should_run and printer:
            status["vision_ai"] = vision_monitor.check(printer_id, printer_dict_to_config(printer_id, printer), cfg, status=status, force=force)
        else:
            cached = vision_monitor.cached_result(printer_id)
            if cached:
                status["vision_ai"] = cached
            else:
                status["vision_ai"] = {
                    "enabled": True,
                    "visual_state": "pending",
                    "summary": "Waiting for the background watchdog to run the first vision check.",
                    "last_check_epoch": None,
                    "last_check": None,
                }
    except Exception as exc:
        status["vision_ai"] = {
            "enabled": True,
            "ok": False,
            "visual_state": "camera_bad",
            "summary": f"Vision monitor error: {exc}",
            "last_error": str(exc),
            "last_check_epoch": time.time(),
            "last_check": time.strftime("%H:%M:%S"),
        }
    return status


def _attach_ai_status(printer_id: str, status: dict[str, Any], snap: Optional[dict[str, Any]], cfg: dict[str, Any], ai_source: str = "request", force_ai_evaluate: bool = False, printer: dict[str, Any] | None = None) -> dict[str, Any]:
    ai_cfg = cfg.get("portal_ai", {}) or {}
    if ai_cfg.get("enabled", True) and ai_cfg.get("monitor_active_prints_only", True) and not bool(status.get("active_print")):
        if ai_cfg.get("vision_ai_enabled", False):
            status["vision_ai"] = _idle_vision_result(printer_id, ai_source)
        status["portal_ai"] = _idle_ai_result(printer_id, status, cfg, ai_source)
        return status
    use_cached = (
        ai_source == "request"
        and ai_cfg.get("enabled", True)
        and ai_cfg.get("background_monitor_enabled", True)
        and not force_ai_evaluate
    )
    if use_cached:
        cached = portal_ai.cached_result(printer_id)
        max_age = max(90.0, float(ai_cfg.get("check_interval_seconds") or 30) * 3.0)
        if cached and (time.time() - float(cached.get("last_check_epoch") or 0)) <= max_age:
            cached["served_from_cache"] = True
            cached["background_monitor_enabled"] = True
            vision_cached = vision_monitor.cached_result(printer_id)
            if vision_cached:
                status["vision_ai"] = vision_cached
                cached.setdefault("vision", vision_cached)
            status["portal_ai"] = cached
            return status
    status = _maybe_attach_vision(printer_id, printer, status, cfg, ai_source=ai_source, force=force_ai_evaluate)
    status["portal_ai"] = portal_ai.evaluate(printer_id, status, snap, cfg, source=ai_source)
    return status


def _status_from_snapshot(printer_id: str, printer: dict[str, Any], snap: Optional[dict[str, Any]], ai_source: str = "request", force_ai_evaluate: bool = False, attach_ai: bool = True) -> dict[str, Any]:
    pcfg = printer_dict_to_config(printer_id, printer)
    if not snap:
        cfg = load_config()
        status = PrinterClient(printer_id, printer, cfg)._empty_status("CC2 client is not running", reachable=False)
        if not attach_ai:
            return status
        return _attach_ai_status(printer_id, status, None, cfg, ai_source=ai_source, force_ai_evaluate=force_ai_evaluate, printer=printer)
    n = snap.get("normalized") or {}
    temps = n.get("temps") or {}
    nozzle = temps.get("nozzle") or {}
    bed = temps.get("bed") or {}
    position = n.get("position") or {}
    speed_mode = position.get("speed_mode")
    speed_raw = position.get("speed")
    speed_mode_name = position.get("speed_mode_name")
    speed_percent = position.get("speed_percent")
    speed_label = _speed_label(speed_mode, speed_raw, speed_percent)
    progress = n.get("progress") or 0
    try:
        progress = float(progress)
        if progress <= 1:
            progress *= 100.0
        progress = max(0, min(100, progress))
    except Exception:
        progress = 0.0
    print_metrics = _extract_print_metrics(snap, n)
    state = n.get("sub_state") or n.get("state") or ("registered" if snap.get("registered") else "offline")
    reachable = bool(snap.get("connected") or snap.get("registered"))
    status = {
        "printer_id": printer_id,
        "name": pcfg.name,
        "host": pcfg.host,
        "serial": pcfg.serial,
        "reachable": reachable,
        "connected": bool(snap.get("connected")),
        "registered": bool(snap.get("registered")),
        "state": str(state).lower(),
        "status_text": str(state).replace("_", " ").title(),
        "message": snap.get("last_error") or ("Registered with printer" if snap.get("registered") else "Waiting for MQTT registration"),
        "progress": round(progress, 1),
        "print_time": seconds_to_hms((n.get("time") or {}).get("elapsed_sec")) or "-",
        "time_left": (n.get("time") or {}).get("remaining_human") or seconds_to_hms((n.get("time") or {}).get("remaining_sec")) or "-",
        "completion": f"{round(progress, 1)}%",
        "speed_mode": speed_mode,
        "speed_mode_name": speed_mode_name,
        "speed_raw": speed_raw,
        "speed_percent": speed_percent,
        "speed_setting": speed_label,
        "filament_used": print_metrics.get("filament_used") or "-",
        "filament_used_raw": print_metrics.get("filament_used_raw"),
        "filament_used_source": print_metrics.get("filament_used_source"),
        "layer_current": print_metrics.get("layer_current"),
        "layer_total": print_metrics.get("layer_total"),
        "layer_progress": print_metrics.get("layer_progress") or "-",
        "hotend_current": nozzle.get("actual"),
        "hotend_target": nozzle.get("target"),
        "bed_current": bed.get("actual"),
        "bed_target": bed.get("target"),
        "file": n.get("file") or "-",
        "gcode_thumbnail_url": None,
        "show_gcode_thumbnail": bool((load_config().get("dashboard") or {}).get("show_gcode_thumbnail", True)),
        "updated_at": snap.get("last_message_age_sec"),
        "camera_url": f"/api/printers/{printer_id}/camera/stream",
        "camera_snapshot_url": f"/api/printers/{printer_id}/camera/snapshot.jpg",
        "camera_status_url": f"/api/printers/{printer_id}/camera/status",
        "direct_camera_url": f"http://{pcfg.host}:8080/",
        "camera_relay": camera_relays.get(printer_id, pcfg).status(),
        "portal_url": f"/portal-fullscreen?printer={printer_id}",
        "portal_chrome_url": f"/portal?printer={printer_id}",
        "kiosk_url": f"/kiosk?printer={printer_id}",
        "direct_portal_url": f"http://{pcfg.host}/",
        "raw": snap,
    }
    status["active_print"] = _status_looks_active_print(status, snap)
    if status.get("show_gcode_thumbnail") and _has_real_file(status.get("file")):
        status["gcode_thumbnail_url"] = f"/api/printers/{printer_id}/files/thumbnail-image?filename={quote(str(status.get('file') or ''))}&storage_media=local"
    if not attach_ai:
        return status
    return _attach_ai_status(printer_id, status, snap, load_config(), ai_source=ai_source, force_ai_evaluate=force_ai_evaluate, printer=printer)


def _attach_cached_ai_for_kiosk(printer_id: str, status: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Attach cached AI/vision data only.

    Kiosk refreshes should be tiny and fast. The normal /api/status route may
    compute rule-engine state when the cache is missing/stale; that is fine for
    the dashboard, but a fullscreen camera page should never hold the camera
    placeholder hostage while AI or telemetry rules warm up.
    """
    ai_cfg = cfg.get("portal_ai", {}) or {}
    vision_cached = vision_monitor.cached_result(printer_id)
    if vision_cached:
        status["vision_ai"] = vision_cached
    cached = portal_ai.cached_result(printer_id)
    if cached:
        out = dict(cached)
        out["served_from_cache"] = True
        out["kiosk_fast_path"] = True
        if vision_cached:
            out.setdefault("vision", vision_cached)
        status["portal_ai"] = out
    else:
        status["portal_ai"] = {
            "enabled": bool(ai_cfg.get("enabled", True)),
            "state": "standing_by",
            "level": "low" if status.get("reachable") else "watch",
            "risk": 0 if status.get("reachable") else 35,
            "summary": "Standing By" if status.get("reachable") else "Waiting for printer telemetry",
            "reasons": ["Kiosk is using the fast cached AI path; background AI will update this badge when available."],
            "last_check_epoch": None,
            "last_check": None,
            "kiosk_fast_path": True,
        }
        if vision_cached:
            status["portal_ai"]["vision"] = vision_cached
    return status


def _kiosk_status_for_printer(printer_id: str, printer: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    pcfg = printer_dict_to_config(printer_id, printer)
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, pcfg)
    snap = runtime.snapshot(printer_id)
    status = _status_from_snapshot(printer_id, printer, snap, ai_source="kiosk", force_ai_evaluate=False, attach_ai=False)
    # Empty/no-MQTT snapshots come from the generic PrinterClient placeholder,
    # so make sure kiosk still receives the relayed camera URLs and relay state.
    status.update({
        "printer_id": printer_id,
        "name": status.get("name") or pcfg.name,
        "host": status.get("host") or pcfg.host,
        "camera_url": f"/api/printers/{printer_id}/camera/stream",
        "camera_snapshot_url": f"/api/printers/{printer_id}/camera/snapshot.jpg",
        "camera_status_url": f"/api/printers/{printer_id}/camera/status",
        "direct_camera_url": f"http://{pcfg.host}:8080/",
        "camera_relay": camera_relays.get(printer_id, pcfg).status(),
        "portal_url": f"/portal-fullscreen?printer={printer_id}",
        "portal_chrome_url": f"/portal?printer={printer_id}",
        "kiosk_url": f"/kiosk?printer={printer_id}",
        "direct_portal_url": f"http://{pcfg.host}/",
    })
    return _attach_cached_ai_for_kiosk(printer_id, status, cfg)


@app.get("/api/kiosk/status")
async def api_kiosk_status():
    cfg = load_config()
    pid, printer = default_printer(cfg)
    if not pid or not printer:
        raise HTTPException(status_code=404, detail="No printer configured")
    return _kiosk_status_for_printer(pid, printer)


@app.get("/api/kiosk/status/{printer_id}")
async def api_kiosk_status_printer(printer_id: str):
    cfg = load_config()
    printer = cfg.get("printers", {}).get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not configured")
    return _kiosk_status_for_printer(printer_id, printer)


@app.get("/api/status")
async def api_status():
    cfg = load_config()
    pid, printer = default_printer(cfg)
    if not pid or not printer:
        raise HTTPException(status_code=404, detail="No printer configured")
    if not runtime.get_client(pid):
        runtime.start(pid, printer_dict_to_config(pid, printer))
    snap = runtime.snapshot(pid)
    return _status_from_snapshot(pid, printer, snap)


@app.get("/api/status/{printer_id}")
async def api_status_printer(printer_id: str):
    cfg = load_config()
    printer = cfg.get("printers", {}).get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
    snap = runtime.snapshot(printer_id)
    return _status_from_snapshot(printer_id, printer, snap)


@app.get("/api/ai/monitor")
async def api_ai_monitor_status():
    cfg = load_config()
    ai_cfg = cfg.get("portal_ai", {}) or {}
    cached = {}
    for printer_id in (cfg.get("printers") or {}).keys():
        cached[printer_id] = portal_ai.cached_result(printer_id)
    return {
        "ok": True,
        "running": bool(_AI_MONITOR_TASK and not _AI_MONITOR_TASK.done()),
        "state": _AI_MONITOR_STATE,
        "config": {
            "enabled": bool(ai_cfg.get("enabled", True)),
            "background_monitor_enabled": bool(ai_cfg.get("background_monitor_enabled", True)),
            "check_interval_seconds": ai_cfg.get("check_interval_seconds", 30),
            "background_log_changes": bool(ai_cfg.get("background_log_changes", True)),
            "background_min_log_level": ai_cfg.get("background_min_log_level", "watch"),
            "vision_ai_enabled": bool(ai_cfg.get("vision_ai_enabled", False)),
            "ollama_base_url": ai_cfg.get("ollama_base_url"),
            "ollama_vision_model": ai_cfg.get("ollama_vision_model"),
            "vision_check_interval_seconds": ai_cfg.get("vision_check_interval_seconds"),
        },
        "cached": cached,
    }


@app.get("/api/printers/{printer_id}/ai/status")
async def api_ai_status(printer_id: str):
    cfg = load_config()
    printer = cfg.get("printers", {}).get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
    snap = runtime.snapshot(printer_id)
    status = _status_from_snapshot(printer_id, printer, snap)
    return {"ok": True, "portal_ai": status.get("portal_ai"), "status": status}


@app.post("/api/printers/{printer_id}/ai/check-now")
async def api_ai_check_now(printer_id: str):
    portal_ai.reset(printer_id)
    return await api_ai_status(printer_id)


def _trim_raw_status(status: dict[str, Any] | None) -> dict[str, Any]:
    """Return the useful status fields without embedding the full raw MQTT snapshot."""
    if not isinstance(status, dict):
        return {}
    omit = {"raw"}
    return {k: v for k, v in status.items() if k not in omit}


def _copy_feedback_frame(printer_id: str, label: str) -> dict[str, Any] | None:
    """Fallback: copy the latest vision frame into the feedback dataset folder."""
    try:
        src = vision_monitor.latest_frame_path(printer_id)
        if not src.exists():
            return None
        safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(label or "feedback")).strip("-") or "feedback"
        root = DATA_DIR / "ai_feedback_frames" / printer_id
        root.mkdir(parents=True, exist_ok=True)
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}_{safe_label}_{uuid.uuid4().hex[:8]}"
        dest = root / f"{stem}.jpg"
        shutil.copy2(src, dest)
        return {
            "captured": True,
            "fresh": False,
            "source": "cached_latest_frame_fallback",
            "source_path": str(src),
            "path": str(dest),
            "relative_path": str(dest.relative_to(DATA_DIR)) if DATA_DIR in dest.parents else str(dest),
            "bytes": dest.stat().st_size,
        }
    except Exception as exc:
        return {"captured": False, "fresh": False, "error": str(exc)}


def _capture_feedback_frame(printer_id: str, printer: dict[str, Any], cfg: dict[str, Any], label: str) -> dict[str, Any] | None:
    """Prefer a fresh camera capture for feedback; fall back to latest.jpg if needed."""
    try:
        return vision_monitor.capture_feedback_frame(printer_id, printer_dict_to_config(printer_id, printer), cfg, label)
    except Exception as exc:
        fallback = _copy_feedback_frame(printer_id, label)
        if fallback:
            fallback.setdefault("fresh_capture_error", str(exc))
            return fallback
        return {"captured": False, "fresh": False, "error": str(exc)}


def _feedback_kind(label: str) -> str:
    label = str(label or "").strip().lower()
    if label in {"looks_good", "good", "ok"}:
        return "positive"
    if label in {"looks_bad", "bad", "failure", "problem"}:
        return "failure"
    if label in {"false_alarm", "false-positive", "false_positive"}:
        return "false_alarm"
    return "unknown"




def _read_feedback_rows(limit: int = 200) -> list[dict[str, Any]]:
    path = DATA_DIR / "ai_feedback.jsonl"
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()[-max(1, min(limit, 2000)):]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
            except Exception:
                continue
    except Exception:
        return []
    return rows


async def _run_learning_rebuild(printer_id: str, cfg: dict[str, Any]) -> None:
    try:
        await asyncio.to_thread(ai_learning.rebuild_profile, printer_id, cfg)
    except Exception as exc:
        log("warning", f"AI learning profile rebuild failed: {exc}", "portal_ai", printer=printer_id)


def _schedule_learning_rebuild(printer_id: str, cfg: dict[str, Any]) -> None:
    try:
        asyncio.create_task(_run_learning_rebuild(printer_id, cfg))
    except RuntimeError:
        try:
            ai_learning.rebuild_profile(printer_id, cfg)
        except Exception as exc:
            log("warning", f"AI learning profile rebuild failed: {exc}", "portal_ai", printer=printer_id)


@app.get("/api/ai/feedback/recent")
async def api_ai_feedback_recent(limit: int = Query(50, ge=1, le=500)):
    rows = _read_feedback_rows(limit)
    if not rows:
        rows = portal_ai.recent_feedback(limit)
    return {"ok": True, "count": len(rows), "feedback": rows[-limit:]}


@app.get("/api/ai/feedback/stats")
async def api_ai_feedback_stats(limit: int = Query(500, ge=1, le=2000)):
    rows = _read_feedback_rows(limit)
    stats = feedback_stats(rows)
    return {
        "ok": True,
        "total": len(rows),
        "labels": stats.get("labels", {}),
        "kinds": stats.get("kinds", {}),
        "outcomes": stats.get("outcomes", {}),
        "printers": stats.get("printers", {}),
        "frames": stats.get("frames", 0),
        "active_suppressions": stats.get("suppressions", 0),
        "used_for_live_decisions": True,
        "live_decision_use": "false-positive feedback can suppress similar low/severity warnings for the current active print only",
        "threshold_auto_tuning": False,
        "note": "Feedback is used for review data, confusion-matrix stats, and temporary same-print false-alarm suppression. It does not overwrite heuristic threshold settings.",
    }


@app.get("/api/ai/feedback/suppressions")
async def api_ai_feedback_suppressions(printer_id: str | None = None):
    items = current_suppressions(printer_id)
    return {"ok": True, "count": len(items), "suppressions": items}


@app.get("/api/ai/learning/status")
async def api_ai_learning_status():
    cfg = load_config()
    return await asyncio.to_thread(ai_learning.global_status, cfg)


@app.post("/api/ai/learning/rebuild")
async def api_ai_learning_rebuild():
    cfg = load_config()
    results = []
    for pid in ai_learning.known_printer_ids(cfg):
        results.append(await asyncio.to_thread(ai_learning.rebuild_profile, pid, cfg))
    return {"ok": True, "count": len(results), "profiles": results}


@app.post("/api/ai/learning/reset")
async def api_ai_learning_reset(body: LearningResetRequest | None = None):
    delete_samples = bool(body.delete_samples) if body else False
    result = await asyncio.to_thread(ai_learning.reset_profile, None, delete_samples)
    status = await asyncio.to_thread(ai_learning.global_status, load_config())
    return {"ok": True, "result": result, "status": status}


@app.get("/api/printers/{printer_id}/ai/learning")
async def api_printer_ai_learning(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}) and printer_id not in ai_learning.known_printer_ids(cfg):
        raise HTTPException(status_code=404, detail="Printer not configured")
    return await asyncio.to_thread(ai_learning.profile_status, printer_id, cfg)


@app.post("/api/printers/{printer_id}/ai/learning/rebuild")
async def api_printer_ai_learning_rebuild(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}) and printer_id not in ai_learning.known_printer_ids(cfg):
        raise HTTPException(status_code=404, detail="Printer not configured")
    return await asyncio.to_thread(ai_learning.rebuild_profile, printer_id, cfg)


@app.post("/api/printers/{printer_id}/ai/learning/reset")
async def api_printer_ai_learning_reset(printer_id: str, body: LearningResetRequest | None = None):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}) and printer_id not in ai_learning.known_printer_ids(cfg):
        raise HTTPException(status_code=404, detail="Printer not configured")
    delete_samples = bool(body.delete_samples) if body else False
    result = await asyncio.to_thread(ai_learning.reset_profile, printer_id, delete_samples)
    profile = await asyncio.to_thread(ai_learning.profile_status, printer_id, cfg)
    return {"ok": True, "result": result, "profile": profile}


@app.get("/api/printers/{printer_id}/ai/learning/samples")
async def api_printer_ai_learning_samples(printer_id: str, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}) and printer_id not in ai_learning.known_printer_ids(cfg):
        raise HTTPException(status_code=404, detail="Printer not configured")
    from . import ai_learning_db
    samples = await asyncio.to_thread(ai_learning_db.fetch_recent_samples, printer_id, limit, offset)
    return {"ok": True, "printer_id": printer_id, "count": len(samples), "limit": limit, "offset": offset, "samples": samples}

@app.post("/api/printers/{printer_id}/ai/feedback")
async def api_ai_feedback(printer_id: str, body: AIFeedbackRequest):
    cfg = load_config()
    ai_cfg = cfg.get("portal_ai", {}) or {}
    printer = cfg.get("printers", {}).get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
    snap = runtime.snapshot(printer_id)
    status = _status_from_snapshot(printer_id, printer, snap, ai_source="request", force_ai_evaluate=False)
    portal_cached = portal_ai.cached_result(printer_id) or status.get("portal_ai")
    vision_cached = vision_monitor.cached_result(printer_id) or status.get("vision_ai") or (portal_cached or {}).get("vision")
    frame_info = _capture_feedback_frame(printer_id, printer, cfg, body.label)
    fresh_heuristics = (frame_info or {}).pop("heuristics", None) if isinstance(frame_info, dict) else None
    interpretation = interpret_feedback(body.label, portal_cached, vision_cached)
    suppression = record_feedback_suppression(
        printer_id,
        body.label,
        interpretation,
        status,
        portal_cached,
        vision_cached,
        fresh_heuristics=fresh_heuristics,
        ai_cfg=ai_cfg,
    )
    training_snapshot = {
        "schema": "cc2-ai-feedback-v3",
        "label": str(body.label or "unknown"),
        "kind": _feedback_kind(body.label),
        "note": str(body.note or ""),
        "printer_id": printer_id,
        "created_at_epoch": time.time(),
        "status": _trim_raw_status(status),
        "portal_ai": portal_cached or {},
        "vision": vision_cached or {},
        "fresh_heuristics": fresh_heuristics or {},
        "interpretation": interpretation,
        "suppression": suppression,
        "frame": frame_info,
        "client_context": body.context or {},
        "raw_snapshot_summary": {
            "connected": bool((snap or {}).get("connected")),
            "registered": bool((snap or {}).get("registered")),
            "last_message_age_sec": (snap or {}).get("last_message_age_sec"),
        },
        "training_use": {
            "dataset_ready": bool(frame_info and frame_info.get("captured")),
            "used_for_live_decisions": bool(suppression),
            "threshold_auto_tuning": False,
            "suppression_active": bool(suppression),
            "note": "Saved as labeled review data. False-positive feedback may suppress similar low/severity warnings for this active print only. Heuristic thresholds are not overwritten.",
        },
    }
    row = portal_ai.feedback(printer_id, body.label, body.note, training_snapshot)
    learning_result: dict[str, Any] = {"enabled": False, "inserted": False}
    if bool(ai_cfg.get("ai_feedback_learning_enabled", True)):
        try:
            learning_result = await asyncio.to_thread(ai_learning.record_feedback_row, row)
            learning_result["enabled"] = True
            if bool(ai_cfg.get("ai_learning_rebuild_on_feedback", True)) and learning_result.get("inserted"):
                _schedule_learning_rebuild(printer_id, cfg)
        except Exception as exc:
            learning_result = {"enabled": True, "inserted": False, "error": str(exc)}
            log("warning", f"AI learning feedback mirror failed: {exc}", "portal_ai", printer=printer_id)
    outcome = interpretation.get("outcome")
    sup_msg = "; suppression=active" if suppression else ""
    learn_msg = "; learning=sqlite" if learning_result.get("inserted") else ""
    log("info", f"Portal AI feedback saved: {body.label} ({outcome}); frame={'yes' if frame_info and frame_info.get('captured') else 'no'}{sup_msg}{learn_msg}", "portal_ai", printer=printer_id, label=body.label, outcome=outcome, frame=(frame_info or {}).get("relative_path"))
    return {"ok": True, "feedback": row, "frame": frame_info, "training": training_snapshot.get("training_use"), "interpretation": interpretation, "suppression": suppression, "learning": learning_result}


@app.get("/api/printers/{printer_id}/status")
async def api_legacy_status(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, cfg["printers"][printer_id]))
    return runtime.snapshot(printer_id)


def _send_command(printer_id: str, method: int, params: dict[str, Any] | None = None, wait: bool = True, timeout: float = 10.0, raise_on_result_error: bool = True) -> dict[str, Any]:
    cfg = load_config()
    pdata = (cfg.get("printers") or {}).get(printer_id)
    if not pdata:
        raise HTTPException(404, "Printer not configured")
    pcfg = printer_dict_to_config(printer_id, pdata)
    if not method_allowed(method, pcfg.allow_commands, pcfg.allow_dangerous_commands):
        raise HTTPException(403, "Command blocked by safety settings. Enable allow_commands / allow_dangerous_commands for this printer if you really mean it.")
    client = runtime.get_client(printer_id)
    if not client:
        runtime.start(printer_id, pcfg)
        client = runtime.get_client(printer_id)
    if not client:
        raise HTTPException(409, "Printer client is not running; check host, serial, and PIN/access code.")
    try:
        result = client.send_request(method, params or {}, wait=wait, timeout=timeout, raise_on_error_code=raise_on_result_error)
        return {"ok": True, "result": result}
    except CommandError as exc:
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/printers/{printer_id}/command")
async def api_command(printer_id: str, body: CommandRequest):
    return await asyncio.to_thread(_send_command, printer_id, body.method, body.params, body.wait, body.timeout)


@app.post("/api/action/{action_id}")
async def api_action(action_id: str, req: ActionRequest | None = None):
    cfg = load_config()
    pid = req.printer_id if req and req.printer_id else cfg.get("app", {}).get("default_printer")
    if not pid:
        pid, _ = default_printer(cfg)
    if not pid or pid not in (cfg.get("printers") or {}):
        raise HTTPException(status_code=404, detail="Printer not configured")
    actions = cfg.get("actions", {})
    action_cfg = actions.get(action_id)
    if not action_cfg or not action_cfg.get("enabled", False):
        raise HTTPException(status_code=403, detail="Action disabled")

    method = None
    params: dict[str, Any] = {}
    wait = True
    timeout = 20.0
    if action_id == "light_toggle":
        snap = runtime.snapshot(pid) or {}
        led = ((snap.get("normalized") or {}).get("led") or {}).get("status")
        turn_on = not bool(led)
        method, params = SET_LIGHT, light_params(turn_on)
    elif action_id == "pause_resume":
        snap = runtime.snapshot(pid) or {}
        state = str(((snap.get("normalized") or {}).get("sub_state") or (snap.get("normalized") or {}).get("state") or "")).lower()
        method, params, timeout = (RESUME_PRINT if "pause" in state else PAUSE_PRINT), {}, 60.0
    elif action_id == "cancel_print":
        method, params, timeout = STOP_PRINT, {}, 60.0
    elif action_id == "restart_camera":
        # Wake/enable the webcam, then restart only the cc2-dash relay.
        # Do not create an extra direct browser-style camera stream here.
        try:
            await asyncio.to_thread(_send_command, pid, ENABLE_WEBCAM, webcam_params(True), False, 5.0)
        except Exception:
            pass
        pcfg = printer_dict_to_config(pid, (cfg.get("printers") or {}).get(pid) or {})
        relay = camera_relays.get(pid, pcfg)
        await asyncio.to_thread(relay.restart, _camera_cfg())
        log("info", "Camera relay restart requested", "camera", printer=pid)
        return {"ok": True, "message": "Camera relay restarted", "relay": relay.status()}
    elif action_id == "vision_check_now":
        result = await api_vision_check_now(pid)
        log("info", "Manual Ollama vision check requested", "portal_ai", printer=pid)
        return {"ok": True, "message": "Camera analysis complete", "result": result}
    elif action_id == "set_speed_preset":
        req_params = req.params if req and isinstance(req.params, dict) else {}
        mode = int(req_params.get("mode", 1))
        mode = max(0, min(3, mode))
        method, params, timeout = SET_PRINT_SPEED, print_speed_params(mode), 12.0
    else:
        raise HTTPException(404, f"Unknown action: {action_id}")

    result = await asyncio.to_thread(_send_command, pid, method, params, wait, timeout)
    if action_id == "set_speed_preset":
        req_params = req.params if req and isinstance(req.params, dict) else {}
        mode = int(req_params.get("mode", 1))
        mode = max(0, min(3, mode))
        message = f"Print speed preset set to {SPEED_PRESETS.get(mode, mode)}"
    else:
        message = f"{action_cfg.get('label', action_id)} sent"
    if action_id == "set_speed_preset":
        log("info", message, "command", printer=pid, mode=mode)
    else:
        log("info", f"Action {action_id} sent", "command", printer=pid)
    return {"ok": True, "message": message, "result": result.get("result")}


def _require_printer_running(printer_id: str) -> dict[str, Any]:
    cfg = load_config()
    pdata = (cfg.get("printers") or {}).get(printer_id)
    if not pdata:
        raise HTTPException(404, "Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, pdata))
    return pdata


@app.get("/api/printers/{printer_id}/filaments")
async def api_filaments(printer_id: str, refresh: bool = Query(False)):
    pdata = _require_printer_running(printer_id)
    command_result = None
    # The stock Elegoo filament sync UI requests printer MMS/filament info. In
    # the local MQTT protocol, method 2005 is the CANVAS/MMS status call used by
    # this project already, so it is the safest read path we have.
    if refresh:
        try:
            command_response = await asyncio.to_thread(_send_command, printer_id, GET_CANVAS_STATUS, {}, True, 12.0, False)
            command_result = command_response.get("result", command_response) if isinstance(command_response, dict) else command_response
        except Exception as exc:
            log("warn", f"Filament refresh via CANVAS status failed: {exc}", "filament", printer=printer_id)
    snap = runtime.snapshot(printer_id) or {}
    info = _extract_filament_info(snap, command_result if isinstance(command_result, dict) else None)
    info["printer_config"] = public_printer_dict(printer_dict_to_config(printer_id, pdata), include_secret=False)
    return info


@app.post("/api/printers/{printer_id}/filaments/refresh")
async def api_filaments_refresh(printer_id: str):
    return await api_filaments(printer_id, refresh=True)


@app.post("/api/printers/{printer_id}/filaments/auto-refill")
async def api_filaments_auto_refill(printer_id: str, body: FilamentAutoRefillRequest):
    result = await asyncio.to_thread(_send_command, printer_id, SET_AUTO_REFILL, auto_refill_params(body.enabled), True, 12.0, True)
    log("info", f"Auto filament refill set to {'on' if body.enabled else 'off'}", "filament", printer=printer_id)
    info = await api_filaments(printer_id, refresh=True)
    # The printer can report stale canvas_info for a moment after the command.
    # Keep the fresh report for debugging, but reflect the successful requested
    # state immediately in the UI; the frontend performs another refresh shortly
    # after this response to reconcile with firmware.
    info["reported_auto_refill"] = info.get("auto_refill")
    info["auto_refill"] = bool(body.enabled)
    info["command_result"] = result
    info["requested_auto_refill"] = body.enabled
    return info


@app.post("/api/printers/{printer_id}/filaments/load")
async def api_filaments_load(printer_id: str, body: FilamentMotionRequest):
    _require_filament_idle(printer_id)
    params = filament_motion_params(body.canvas_id, body.tray_id)
    result = await asyncio.to_thread(_send_command, printer_id, LOAD_FILAMENT, params, True, 300.0, True)
    log("info", f"Requested CANVAS load for slot {params.get('tray_id')}", "filament", printer=printer_id)
    info = await api_filaments(printer_id, refresh=True)
    info["command_result"] = result
    info["requested_action"] = "load"
    info["requested_params"] = params
    return info


@app.post("/api/printers/{printer_id}/filaments/unload")
async def api_filaments_unload(printer_id: str, body: FilamentMotionRequest):
    _require_filament_idle(printer_id)
    params = filament_motion_params(body.canvas_id, body.tray_id)
    result = await asyncio.to_thread(_send_command, printer_id, UNLOAD_FILAMENT, params, True, 300.0, True)
    log("info", f"Requested CANVAS unload for slot {params.get('tray_id')}", "filament", printer=printer_id)
    info = await api_filaments(printer_id, refresh=True)
    info["command_result"] = result
    info["requested_action"] = "unload"
    info["requested_params"] = params
    return info


@app.post("/api/printers/{printer_id}/filaments/edit")
async def api_filaments_edit(printer_id: str, body: FilamentInfoRequest):
    _require_filament_idle(printer_id)
    params = filament_info_params(model_to_dict(body))
    result = await asyncio.to_thread(_send_command, printer_id, SET_FILAMENT_INFO, params, True, 20.0, True)
    log("info", f"Updated CANVAS slot {params.get('tray_id')} filament to {params.get('filament_name')} {params.get('filament_color')}", "filament", printer=printer_id)
    info = await api_filaments(printer_id, refresh=True)
    info["command_result"] = result
    info["requested_action"] = "edit"
    info["requested_params"] = params
    return info


@app.post("/api/printers/{printer_id}/filaments/mono/edit")
async def api_filaments_mono_edit(printer_id: str, body: FilamentInfoRequest):
    _require_filament_idle(printer_id)
    params = mono_filament_info_params(model_to_dict(body))
    result = await asyncio.to_thread(_send_command, printer_id, SET_MONO_FILAMENT_INFO, params, True, 20.0, True)
    log("info", f"Updated mono filament to {params.get('filament_name')} {params.get('filament_color')}", "filament", printer=printer_id)
    info = await api_filaments(printer_id, refresh=True)
    info["command_result"] = result
    info["requested_action"] = "mono_edit"
    info["requested_params"] = params
    return info


@app.get("/api/printers/{printer_id}/filaments/mono")
async def api_filaments_mono(printer_id: str):
    result = await asyncio.to_thread(_send_command, printer_id, GET_MONO_FILAMENT_INFO, {}, True, 12.0, False)
    return result


@app.get("/api/printers/{printer_id}/files")
async def api_files(printer_id: str, path: str = "/", storage_media: str = "local", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), offset: Optional[int] = None, limit: Optional[int] = None):
    media = normalize_storage_media(storage_media)
    directory = normalize_file_dir(path)
    payload = await asyncio.to_thread(
        _send_command,
        printer_id,
        GET_FILE_LIST,
        file_list_params(directory, media, page, page_size, offset, limit),
        True,
        15.0,
        False,
    )
    return _normalize_file_response(payload, media, directory)


@app.get("/api/printers/{printer_id}/files/detail")
async def api_file_detail(printer_id: str, filename: str, storage_media: str = "local", directory: Optional[str] = None):
    return await asyncio.to_thread(_send_command, printer_id, GET_FILE_DETAIL, file_detail_params(filename, storage_media, directory), True, 15.0)


@app.get("/api/printers/{printer_id}/files/thumbnail")
async def api_file_thumbnail(printer_id: str, filename: str, storage_media: str = "local"):
    return await asyncio.to_thread(_send_command, printer_id, GET_FILE_THUMBNAIL, file_thumbnail_params(filename, storage_media), True, 15.0)


@app.get("/api/printers/{printer_id}/files/thumbnail-image")
async def api_file_thumbnail_image(printer_id: str, filename: str, storage_media: str = "local"):
    """Return a G-code thumbnail as an actual image when firmware provides one.

    The stock portal accepts several response shapes: the thumbnail may arrive
    from method 1045, from file detail 1046, as a data URL, or as raw base64.
    This proxy normalizes those into an <img>-friendly response and returns 404
    when the active file simply has no thumbnail.
    """
    for method, params, timeout in (
        (GET_FILE_THUMBNAIL, file_thumbnail_params(filename, storage_media), 15.0),
        (GET_FILE_DETAIL, file_detail_params(filename, storage_media), 15.0),
    ):
        try:
            payload = await asyncio.to_thread(_send_command, printer_id, method, params, True, timeout, False)
        except Exception as exc:
            log("debug", f"Thumbnail command {method} failed for {filename}: {exc}", "command", printer=printer_id)
            continue
        data, media_type, redirect_url = _extract_thumbnail_image(payload)
        if data and media_type:
            return Response(content=data, media_type=media_type, headers={"Cache-Control": "no-store"})
        if redirect_url:
            cfg = load_config()
            pdata = (cfg.get("printers") or {}).get(printer_id)
            if pdata and not redirect_url.startswith(("http://", "https://", "//")):
                redirect_url = _absolute_printer_url(printer_dict_to_config(printer_id, pdata), redirect_url)
            return RedirectResponse(redirect_url)
    raise HTTPException(404, "No G-code thumbnail returned for this file")


@app.post("/api/printers/{printer_id}/files/delete")
async def api_file_delete(printer_id: str, body: DeleteFileRequest):
    return await asyncio.to_thread(_send_command, printer_id, DELETE_FILE, delete_file_params(body.file_path, body.storage_media), True, 15.0)


@app.post("/api/printers/{printer_id}/files/start")
async def api_file_start(printer_id: str, body: StartPrintRequest):
    return await asyncio.to_thread(
        _send_command,
        printer_id,
        START_PRINT,
        start_print_params(body.filename, body.storage_media, body.start_layer, body.calibration, body.platform_type, body.timelapse),
        True,
        20.0,
    )


@app.get("/api/printers/{printer_id}/disk")
async def api_disk(printer_id: str, storage_media: str = "local"):
    return await asyncio.to_thread(_send_command, printer_id, GET_DISK_INFO, {"storage_media": normalize_storage_media(storage_media)}, True, 10.0)


@app.get("/api/printers/{printer_id}/canvas")
async def api_canvas(printer_id: str):
    return await asyncio.to_thread(_send_command, printer_id, GET_CANVAS_STATUS, {}, True, 10.0)



def _unwrap_command_payload(payload: Any) -> Any:
    """Accept cc2-dash command wrappers and raw firmware replies."""
    root = payload
    if isinstance(root, dict) and "result" in root:
        root = root.get("result")
    if isinstance(root, dict) and "result" in root and len(root) <= 3:
        inner = root.get("result")
        if isinstance(inner, (dict, list)):
            root = inner
    return root


def _first_array(root: Any, candidate_keys: list[str]) -> list[Any]:
    if isinstance(root, list):
        return root
    if not isinstance(root, dict):
        return []
    for key in candidate_keys:
        val = root.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            nested = _first_array(val, candidate_keys)
            if nested:
                return nested
    for val in root.values():
        if isinstance(val, dict):
            nested = _first_array(val, candidate_keys)
            if nested:
                return nested
    return []


def _field(d: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in d and d.get(name) not in (None, ""):
            return d.get(name)
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _error_code(root: Any) -> int:
    if not isinstance(root, dict):
        return 0
    return _as_int(root.get("error_code") or root.get("ErrorCode"), 0)


def _total_from_root(root: Any, fallback: int = 0) -> int:
    if not isinstance(root, dict):
        return fallback
    return _as_int(_field(root, "total", "Total", "total_count", "TotalCount", "count", "Count", default=fallback), fallback)


def _is_gcode_name(name: Any) -> bool:
    return str(name or "").strip().lower().endswith((".gcode", ".gco", ".g"))


def _is_folder_record(item: dict[str, Any]) -> bool:
    kind = str(_field(item, "type", "file_type", "FileType", "fileType", "kind", "Kind", default="") or "").lower()
    if kind in {"folder", "dir", "directory"}:
        return True
    value = _field(item, "is_dir", "IsDir", "isDirectory", "is_directory", "is_folder", "IsFolder", default=None)
    if isinstance(value, bool):
        return value
    if value not in (None, ""):
        return str(value).strip().lower() in {"1", "true", "yes", "folder", "dir"}
    name = str(_field(item, "filename", "file_name", "fileName", "FileName", "name", "Name", "path", "Path", default="") or "")
    return bool(name and name.endswith("/"))


def _basename_from_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = text.rstrip("/")
    if not cleaned:
        return "/"
    return cleaned.split("/")[-1]


def _normalize_file_record(item: Any, storage_media: str, directory: str = "/") -> dict[str, Any] | None:
    media = normalize_storage_media(storage_media)
    directory = normalize_file_dir(directory)
    if isinstance(item, str):
        raw_name = item
        raw_path = item
        is_folder = item.endswith("/")
        raw_item: Any = item
        size = 0
        created = modified = ""
    elif isinstance(item, dict):
        raw_name = _field(item, "filename", "file_name", "fileName", "FileName", "name", "Name", default="")
        raw_path = _field(item, "file_path", "filePath", "path", "Path", "url", "Url", default=raw_name)
        if not raw_name:
            raw_name = raw_path
        is_folder = _is_folder_record(item)
        raw_item = item
        size = _field(item, "size", "Size", "file_size", "FileSize", "FileSizeBytes", "fileSize", default=0)
        created = _field(item, "create_time", "CreateTime", "ctime", "CTime", "begin_time", "BeginTime", default="")
        modified = _field(item, "mtime", "MTime", "modified_time", "ModifyTime", "update_time", "UpdateTime", default="")
    else:
        return None
    if not raw_name:
        return None
    name = _basename_from_path(raw_name)
    if name in {"", "/"}:
        name = _basename_from_path(raw_path)
    file_path = str(raw_path or raw_name or "")
    if media == "u-disk" and file_path and not file_path.startswith("/"):
        if directory and directory != "/":
            file_path = f"{directory.rstrip('/')}/{file_path.lstrip('/')}"
        else:
            file_path = "/" + file_path.lstrip("/")
    return {
        "filename": name,
        "name": name,
        "file_path": file_path or name,
        "type": "folder" if is_folder else "file",
        "is_dir": bool(is_folder),
        "is_gcode": _is_gcode_name(name) or _is_gcode_name(file_path),
        "storage_media": media,
        "dir": directory,
        "size": size,
        "file_size": size,
        "create_time": created,
        "modified_time": modified,
        "print_time": _field(raw_item, "print_time", "PrintTime", "duration", "Duration", default="") if isinstance(raw_item, dict) else "",
        "layer": _field(raw_item, "layer", "Layer", "total_layer", "TotalLayer", default="") if isinstance(raw_item, dict) else "",
        "raw": raw_item,
    }


def _extract_file_items(root: Any) -> list[Any]:
    return _first_array(root, [
        "file_list", "FileList", "fileList", "files", "Files", "items", "Items",
        "list", "List", "data", "Data", "FileData", "file_data",
    ])


def _normalize_file_response(payload: Any, storage_media: str, path: str) -> dict[str, Any]:
    media = normalize_storage_media(storage_media)
    directory = normalize_file_dir(path)
    root = _unwrap_command_payload(payload)
    if isinstance(root, dict) and _error_code(root) != 0:
        return {"ok": False, "result": root, "files": [], "total": 0, "storage_media": media, "path": directory}
    raw_items = _extract_file_items(root)
    files = [f for f in (_normalize_file_record(item, media, directory) for item in raw_items) if f]
    files.sort(key=lambda f: (0 if f.get("is_dir") else 1, str(f.get("filename") or "").lower()))
    result = {
        "error_code": 0,
        "storage_media": media,
        "path": directory,
        "offset": _as_int(_field(root, "offset", "Offset", default=0), 0) if isinstance(root, dict) else 0,
        "total": _total_from_root(root, len(files)),
        "file_list": files,
    }
    return {"ok": True, "result": result, "files": files, "total": result["total"], "storage_media": media, "path": directory, "raw": root}


def _normalize_history_record(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    task_id = _field(item, "task_id", "TaskId", "taskId", "id", "Id", default="")
    name = _field(item, "task_name", "TaskName", "filename", "FileName", "file_name", "name", "Name", default="")
    begin = _field(item, "begin_time", "BeginTime", "start_time", "StartTime", "create_time", "CreateTime", default="")
    end = _field(item, "end_time", "EndTime", "finish_time", "FinishTime", default="")
    size = _field(item, "file_size", "FileSize", "size", "Size", "FileSizeBytes", default=0)
    status = _field(item, "task_status", "TaskStatus", "status", "Status", default="")
    video_status = _as_int(_field(item, "time_lapse_video_status", "TimeLapseVideoStatus", "video_status", "VideoStatus", default=0), 0)
    video_url = _field(item, "time_lapse_video_url", "TimeLapseVideoUrl", "video_url", "VideoUrl", "url", "Url", default="")
    return {
        "task_id": task_id,
        "id": task_id,
        "task_name": name or (f"Task {task_id}" if task_id not in (None, "") else "History task"),
        "filename": name,
        "begin_time": begin,
        "end_time": end,
        "task_status": status,
        "file_size": size,
        "print_time": _field(item, "print_time", "PrintTime", "duration", "Duration", default=""),
        "total_layer": _field(item, "total_layer", "TotalLayer", "layer", "Layer", default=""),
        "filament_used": _field(item, "filament_used", "FilamentUsed", "total_filament_used", "TotalFilamentUsed", default=""),
        "time_lapse_video_status": video_status,
        "time_lapse_video_url": video_url,
        "has_timelapse": bool(video_status in (1, 2) or video_url),
        "is_gcode": _is_gcode_name(name),
        "raw": item,
    }


def _sort_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> float:
        raw = row.get("begin_time") or ""
        try:
            return float(raw)
        except Exception:
            return 0.0
    return sorted(rows, key=key, reverse=True)


def _absolute_printer_url(pcfg: Any, url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("//"):
        return "http:" + url
    if not url.startswith("/"):
        url = "/" + url
    return f"http://{pcfg.host}{url}"


def _download_file_name_from_token(token: str) -> str:
    """Return the printer download file_name from a stock portal video token/URL.

    The stock Elegoo portal does not open TimeLapseVideoUrl directly. It calls:
      http://<printer>/download?X-Token=<pin>&file_name=<TimeLapseVideoUrl>
    Some firmware builds may already return a /download?... URL; normalize both
    shapes to the raw file_name so cc2-dash can proxy it reliably.
    """
    token = str(token or "").strip()
    if not token:
        return ""
    try:
        parsed = urlparse(token)
        qs = parse_qs(parsed.query or "")
        for key in ("file_name", "filename", "file", "name"):
            values = qs.get(key)
            if values:
                return str(values[0] or "").strip()
        # Absolute URLs that are not /download links are still usually file paths
        # on the printer. Keep path+query minus the host so /download receives the
        # printer's expected file token.
        if parsed.scheme and parsed.netloc:
            return (parsed.path or "").lstrip("/") or token
    except Exception:
        pass
    return token


def _stock_download_url(pcfg: Any, file_name: str, media: str = "local") -> str:
    media = str(media or "local").lower()
    endpoint = {
        "local": "/download",
        "u-disk": "/download/udisk",
        "udisk": "/download/udisk",
        "usb": "/download/udisk",
        "sdcard": "/download/sdcard",
        "sd-card": "/download/sdcard",
    }.get(media, "/download")
    return f"http://{pcfg.host}{endpoint}?X-Token={quote(str(pcfg.access_code or ''), safe='')}&file_name={quote(str(file_name or ''), safe='')}"


def _timelapse_proxy_download_url(printer_id: str, file_name: str, media: str = "local") -> str:
    return f"/api/printers/{quote(str(printer_id), safe='')}/timelapse/download?file_name={quote(str(file_name or ''), safe='')}&media={quote(str(media or 'local'), safe='')}"


def _normalize_timelapse_record(item: Any, pcfg: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    task_id = _field(item, "task_id", "TaskId", "taskId", "id", "Id")
    name = _field(item, "task_name", "TaskName", "filename", "FileName", "name", "Name", default="")
    status = _as_int(_field(item, "time_lapse_video_status", "TimeLapseVideoStatus", "video_status", "VideoStatus", default=0), 0)
    url = str(_field(item, "time_lapse_video_url", "TimeLapseVideoUrl", "video_url", "VideoUrl", "url", "Url", default="") or "")
    download_file_name = _download_file_name_from_token(url)
    size = _field(item, "time_lapse_video_size", "TimeLapseVideoSize", "video_size", "VideoSize", "file_size", "FileSize", "size", "Size", default=0)
    duration = _field(item, "time_lapse_video_duration", "TimeLapseVideoDuration", "video_duration", "VideoDuration", "duration", "Duration", default=0)
    begin = _field(item, "begin_time", "BeginTime", "create_time", "CreateTime", "start_time", "StartTime", "ctime", "CTime", default="")
    end = _field(item, "end_time", "EndTime", "finish_time", "FinishTime", default="")
    # Stock portal Video List includes statuses 1 (captured/not generated) and 2 (generated).
    # Include rows with a direct URL/size/duration too, because some firmware uses different status codes.
    has_video_marker = status in (1, 2) or bool(url) or _as_float(size, 0) > 0 or _as_float(duration, 0) > 0
    if not has_video_marker:
        return None
    return {
        "task_id": task_id,
        "task_name": name or f"Task {task_id}",
        "begin_time": begin,
        "end_time": end,
        "task_status": _field(item, "task_status", "TaskStatus", "status", "Status", default=""),
        "time_lapse_video_status": status,
        "time_lapse_video_url": url,
        "download_file_name": download_file_name,
        "download_url": _timelapse_proxy_download_url(pcfg.id, download_file_name) if download_file_name else "",
        "direct_download_url": _stock_download_url(pcfg, download_file_name) if download_file_name else "",
        "time_lapse_video_size": size,
        "time_lapse_video_duration": duration,
        "raw": item,
    }


def _extract_history_items(root: Any) -> list[Any]:
    return _first_array(root, [
        "history_task_list", "HistoryTaskList", "historyTaskList", "task_list", "TaskList",
        "tasks", "Tasks", "items", "Items", "list", "List", "data", "Data",
        "HistoryDetailList", "history_detail_list", "HistoryData", "history_data",
    ])


def _try_history_details(printer_id: str, ids: list[Any]) -> list[Any]:
    ids = [x for x in ids if x not in (None, "")]
    if not ids:
        return []
    # Mirror the stock local-websocket behavior first: CmdGetTaskDetails uses {Id:[...]}.
    shapes = [
        history_detail_params(ids),
        {"id": ids},
        {"task_id": ids},
        {"task_ids": ids},
        {"list": ids},
    ]
    for params in shapes:
        try:
            detail_payload = _send_command(printer_id, GET_HISTORY_TASK_DETAIL, params, True, 12.0, False)
            root = _unwrap_command_payload(detail_payload)
            # Ignore printer-level error replies such as error_code 1003.
            if isinstance(root, dict) and _as_int(root.get("error_code") or root.get("ErrorCode"), 0) != 0:
                continue
            arr = _first_array(root, ["HistoryDetailList", "history_detail_list", "details", "Details", "items", "Items", "data", "Data", "list", "List"])
            if arr:
                return arr
        except Exception:
            continue
    return []


@app.get("/api/printers/{printer_id}/history/list")
async def api_history_list(printer_id: str, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=300), include_details: bool = Query(False)):
    payload = await asyncio.to_thread(_send_command, printer_id, GET_HISTORY_TASK, {}, True, 20.0, False)
    root = _unwrap_command_payload(payload)
    if isinstance(root, dict) and _error_code(root) != 0:
        return {"ok": False, "result": root, "history": [], "total": 0}
    items = _extract_history_items(root)
    detail_items: list[Any] = []
    if include_details and items:
        ids = [_field(item, "task_id", "TaskId", "taskId", "id", "Id") for item in items if isinstance(item, dict)]
        detail_items = await asyncio.to_thread(_try_history_details, printer_id, ids[:80])
        if detail_items:
            items = detail_items
    rows = [row for row in (_normalize_history_record(item) for item in items) if row]
    rows = _sort_history(rows)
    start = max(0, (int(page or 1) - 1) * int(page_size or 100))
    end = start + int(page_size or 100)
    result = {
        "error_code": 0,
        "total": len(rows),
        "raw_history_total": len(_extract_history_items(root)),
        "raw_detail_total": len(detail_items),
        "history_task_list": rows[start:end],
    }
    return {"ok": True, "result": result, "history": rows[start:end], "total": len(rows)}


@app.get("/api/printers/{printer_id}/history")
async def api_history(printer_id: str):
    return await asyncio.to_thread(_send_command, printer_id, GET_HISTORY_TASK, {}, True, 20.0, False)


@app.get("/api/printers/{printer_id}/timelapse")
async def api_timelapse(printer_id: str):
    # The stock Elegoo portal's "Video List" is derived from Print History, not
    # the file-list endpoint. It filters history rows where TimeLapseVideoStatus
    # is 1 (captured but not generated) or 2 (generated), then export/downloads
    # with method 1051 when needed.
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")

    history_payload = await asyncio.to_thread(_send_command, printer_id, GET_HISTORY_TASK, {}, True, 20.0, False)
    root = _unwrap_command_payload(history_payload)
    if isinstance(root, dict) and _as_int(root.get("error_code") or root.get("ErrorCode"), 0) != 0:
        return {"ok": False, "result": root, "videos": [], "total": 0}

    history_items = _extract_history_items(root)
    videos = [v for v in (_normalize_timelapse_record(item, pcfg) for item in history_items) if v]

    # Some stock-web portal paths first fetch task ids, then task details. Try the
    # same pattern if the basic history payload contains no video-marked records.
    detail_items: list[Any] = []
    if not videos and history_items:
        ids: list[Any] = []
        for item in history_items[:80]:
            if isinstance(item, dict):
                ids.append(_field(item, "task_id", "TaskId", "taskId", "id", "Id"))
            else:
                ids.append(item)
        detail_items = await asyncio.to_thread(_try_history_details, printer_id, ids)
        videos = [v for v in (_normalize_timelapse_record(item, pcfg) for item in detail_items) if v]

    result = {
        "error_code": 0,
        "total": len(videos),
        "raw_history_total": len(history_items),
        "raw_detail_total": len(detail_items),
        "videos": videos,
    }
    return {"ok": True, "result": result, "videos": videos, "total": len(videos)}


@app.get("/api/printers/{printer_id}/timelapse/download")
async def api_timelapse_download(printer_id: str, file_name: str = Query(..., min_length=1), media: str = Query("local")):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    file_name = _download_file_name_from_token(file_name)
    if not file_name:
        raise HTTPException(400, "Missing timelapse file name")

    media_key = str(media or "local").lower()
    endpoint = {
        "local": "/download",
        "u-disk": "/download/udisk",
        "udisk": "/download/udisk",
        "usb": "/download/udisk",
        "sdcard": "/download/sdcard",
        "sd-card": "/download/sdcard",
    }.get(media_key, "/download")
    target = f"http://{pcfg.host}{endpoint}"
    params = {"X-Token": pcfg.access_code, "file_name": file_name}

    client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=8.0), follow_redirects=True)
    try:
        req = client.build_request("GET", target, params=params)
        resp = await client.send(req, stream=True)
    except Exception as exc:
        await client.aclose()
        raise HTTPException(502, f"Printer timelapse download failed: {exc}") from exc

    if resp.status_code >= 400:
        text = ""
        try:
            text = (await resp.aread()).decode("utf-8", errors="replace")[:300]
        except Exception:
            text = ""
        await resp.aclose()
        await client.aclose()
        detail = text or f"Printer returned HTTP {resp.status_code} for {endpoint}"
        raise HTTPException(resp.status_code, detail)

    safe_name = Path(file_name).name or "timelapse.mp4"
    safe_name = safe_name.replace("\r", "_").replace("\n", "_")
    headers: dict[str, str] = {}
    for key in ("content-length", "accept-ranges", "etag", "last-modified"):
        if key in resp.headers:
            headers[key] = resp.headers[key]
    headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    media_type = resp.headers.get("content-type") or "video/mp4"

    async def body_iter():
        try:
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(body_iter(), media_type=media_type, headers=headers)


@app.post("/api/printers/{printer_id}/timelapse/export")
async def api_timelapse_export(printer_id: str, body: TimelapseExportRequest):
    token = _download_file_name_from_token(body.url)
    data = await asyncio.to_thread(_send_command, printer_id, GET_TIME_LAPSE_VIDEO_LIST, timelapse_export_params(token), True, 180.0)
    pcfg = _portal_target(printer_id)
    root = _unwrap_command_payload(data)
    returned = ""
    if isinstance(root, dict):
        returned = str(_field(root, "url", "Url", "download_url", "DownloadUrl", "time_lapse_video_url", "TimeLapseVideoUrl", default="") or "")
    download_file_name = _download_file_name_from_token(returned or token)
    if pcfg and download_file_name:
        data["download_file_name"] = download_file_name
        data["download_url"] = _timelapse_proxy_download_url(pcfg.id, download_file_name)
        data["direct_download_url"] = _stock_download_url(pcfg, download_file_name)
        if isinstance(data.get("result"), dict):
            data["result"]["download_file_name"] = download_file_name
            data["result"]["download_url"] = data["download_url"]
            data["result"]["direct_download_url"] = data["direct_download_url"]
    return data


@app.post("/api/printers/{printer_id}/history/delete")
async def api_history_delete(printer_id: str, body: HistoryDeleteRequest):
    return await asyncio.to_thread(_send_command, printer_id, HISTORY_DELETE, history_delete_params(body.task_ids), True, 20.0)


@app.post("/api/printers/{printer_id}/light")
async def api_light(printer_id: str, body: LightRequest):
    return await asyncio.to_thread(_send_command, printer_id, SET_LIGHT, light_params(body.on), True, 10.0)


@app.post("/api/printers/{printer_id}/camera/enable")
async def api_camera_enable(printer_id: str):
    return await asyncio.to_thread(_send_command, printer_id, ENABLE_WEBCAM, webcam_params(True), False, 5.0)


@app.get("/api/vision/models")
async def api_vision_models(base_url: Optional[str] = Query(None)):
    cfg = load_config()
    ai_cfg = dict(cfg.get("portal_ai", {}) or {})
    if base_url:
        ai_cfg["ollama_base_url"] = base_url
    try:
        data = await asyncio.to_thread(vision_monitor.list_ollama_models, ai_cfg)
        return data
    except Exception as exc:
        raise HTTPException(502, f"Could not query Ollama models: {exc}")


@app.post("/api/vision/pull")
async def api_vision_pull(body: OllamaPullRequest):
    cfg = load_config()
    ai_cfg = dict(cfg.get("portal_ai", {}) or {})
    if body.base_url:
        ai_cfg["ollama_base_url"] = body.base_url
    model = (body.model or "").strip()
    if not model:
        raise HTTPException(400, "Model name is required")
    try:
        return await asyncio.to_thread(vision_monitor.pull_ollama_model, ai_cfg, model)
    except Exception as exc:
        raise HTTPException(502, f"Could not pull Ollama model: {exc}")


@app.get("/api/printers/{printer_id}/vision/status")
async def api_vision_status(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    return {"ok": True, "vision": vision_monitor.cached_result(printer_id)}


@app.post("/api/printers/{printer_id}/vision/check-now")
async def api_vision_check_now(printer_id: str):
    cfg = load_config()
    printer = (cfg.get("printers") or {}).get(printer_id)
    if not printer:
        raise HTTPException(404, "Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
    snap = runtime.snapshot(printer_id)
    # Build status without forcing a nested vision run, then run vision explicitly.
    status = _status_from_snapshot(printer_id, printer, snap, ai_source="request", force_ai_evaluate=False, attach_ai=False)
    if (cfg.get("portal_ai", {}) or {}).get("monitor_active_prints_only", True) and not bool(status.get("active_print")):
        result = _idle_vision_result(printer_id, "manual")
        status["vision_ai"] = result
        status["portal_ai"] = _idle_ai_result(printer_id, status, cfg, "manual")
        return {"ok": True, "skipped": True, "reason": "idle", "vision": result, "portal_ai": status.get("portal_ai"), "status": status}
    result = await asyncio.to_thread(
        vision_monitor.check,
        printer_id,
        printer_dict_to_config(printer_id, printer),
        cfg,
        status,
        True,
    )
    portal_ai.reset(printer_id)
    snap = runtime.snapshot(printer_id)
    status = _status_from_snapshot(printer_id, printer, snap, ai_source="request", force_ai_evaluate=False)
    status["vision_ai"] = result
    status["portal_ai"] = portal_ai.evaluate(printer_id, status, snap, cfg, source="request")
    return {"ok": True, "vision": result, "portal_ai": status.get("portal_ai"), "status": status}


@app.get("/api/printers/{printer_id}/vision/latest.jpg")
async def api_vision_latest_frame(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    path = vision_monitor.latest_frame_path(printer_id)
    if not path.exists():
        raise HTTPException(404, "No vision frame has been captured yet")
    return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _camera_cfg() -> dict[str, Any]:
    return camera_proxy_config(load_config())


def _ensure_camera_enabled(printer_id: str) -> None:
    client = runtime.get_client(printer_id)
    if client:
        try:
            client.send_request(ENABLE_WEBCAM, webcam_params(True), wait=False)
        except Exception:
            pass


@app.get("/api/printers/{printer_id}/camera/url")
async def api_camera_url(printer_id: str):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    relay = camera_relays.get(printer_id, pcfg)
    return {
        "url": f"/api/printers/{printer_id}/camera/stream",
        "snapshot_url": f"/api/printers/{printer_id}/camera/snapshot.jpg",
        "status_url": f"/api/printers/{printer_id}/camera/status",
        "direct_url": f"http://{pcfg.host}:8080/",
        "alt_direct_url": f"http://{pcfg.host}:8080/?action=stream",
        "relay": relay.status(),
    }


@app.get("/api/printers/{printer_id}/camera/status")
async def api_camera_status(printer_id: str):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    relay = camera_relays.get(printer_id, pcfg)
    return {"ok": True, "printer": public_printer_dict(pcfg), "relay": relay.status(), "config": _camera_cfg()}


@app.get("/api/camera/status")
async def api_all_camera_status():
    cfg = load_config()
    camera_relays.configure_from_config(cfg)
    return {"ok": True, "relays": camera_relays.status_all(), "config": camera_proxy_config(cfg)}


@app.post("/api/printers/{printer_id}/camera/restart")
async def api_camera_restart(printer_id: str):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    _ensure_camera_enabled(printer_id)
    relay = camera_relays.get(printer_id, pcfg)
    relay.restart(_camera_cfg())
    return {"ok": True, "relay": relay.status()}


@app.get("/api/printers/{printer_id}/camera/snapshot.jpg")
async def api_camera_snapshot(printer_id: str):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    _ensure_camera_enabled(printer_id)
    relay = camera_relays.get(printer_id, pcfg)
    c = _camera_cfg()
    try:
        frame = await asyncio.to_thread(relay.latest_frame, c, float(c.get("stale_frame_seconds") or 10.0) * 3.0, 8.0)
    except Exception as exc:
        raise HTTPException(502, f"Camera snapshot unavailable: {exc}")
    return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.get("/api/printers/{printer_id}/camera/latest.jpg")
async def api_camera_latest(printer_id: str):
    return await api_camera_snapshot(printer_id)


@app.get("/api/printers/{printer_id}/camera/stream")
async def api_camera_stream(printer_id: str):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    _ensure_camera_enabled(printer_id)
    relay = camera_relays.get(printer_id, pcfg)
    c = _camera_cfg()
    if not c.get("enabled", True):
        raise HTTPException(503, "Camera relay is disabled in settings")
    return StreamingResponse(
        relay.stream(c),
        media_type="multipart/x-mixed-replace; boundary=cc2dashframe",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-CC2-Camera-Relay": "1",
        },
    )


@app.get("/api/portal-url")
async def api_portal_url(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        raise HTTPException(404, "No printer configured")
    return {"printer": public_printer_dict(pcfg), "url": f"http://{pcfg.host}/", "index_url": f"http://{pcfg.host}/index", "proxy_url": f"/portal-proxy/{pcfg.id}/", "stock_url": f"/portal-fullscreen?printer={pcfg.id}"}


@app.get("/api/portal-probe")
async def api_portal_probe(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        raise HTTPException(404, "No printer configured")
    candidates = ["/", "/index", "/index.html", "/home", "/home.html", "/web", "/ui", "/dashboard", "/api", "/camera", "/stream", "/webcam", ":8080/", ":8080/?action=stream"]
    out = []
    async with httpx.AsyncClient(timeout=2.5, follow_redirects=False) as client:
        for path in candidates:
            url = f"http://{pcfg.host}{path}" if path.startswith(":") else f"http://{pcfg.host}{path}"
            try:
                r = await client.get(url)
                ctype = r.headers.get("content-type", "")
                text = r.text[:160].replace("\n", " ").replace("\r", " ") if "text" in ctype or "html" in ctype or "json" in ctype else ""
                out.append({"url": url, "status": r.status_code, "content_type": ctype, "server": r.headers.get("server", ""), "location": r.headers.get("location", ""), "sample": text})
            except Exception as exc:
                out.append({"url": url, "error": str(exc)})
    return {"printer": public_printer_dict(pcfg), "results": out}


@app.api_route("/portal-proxy/{printer_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def portal_proxy(printer_id: str, path: str, request: Request):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not found")
    camera_path = (path or "").strip("/").lower()
    camera_query = request.url.query.lower()
    if request.method.upper() in {"GET", "HEAD"} and (
        camera_path in {"camera", "stream", "webcam", "video", "mjpeg", "?action=stream"}
        or camera_path.endswith("/camera")
        or camera_path.endswith("/stream")
        or camera_path.endswith("/webcam")
        or "action=stream" in camera_query
    ):
        return await api_camera_stream(printer_id)

    target = f"http://{pcfg.host}/{path}"
    if request.url.query:
        target += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length", "connection", "accept-encoding"}}
    body = await request.body()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        try:
            r = await client.request(request.method, target, headers=headers, content=body)
        except Exception as exc:
            raise HTTPException(502, f"Printer proxy failed: {exc}")
    excluded = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}
    content = r.content
    ctype = r.headers.get("content-type", "")
    rewrite_enabled = camera_proxy_config(load_config()).get("rewrite_portal_camera_urls", True)
    if any(token in ctype for token in ("text/html", "javascript", "ecmascript", "text/css", "application/json", "text/plain")):
        try:
            text = content.decode(r.encoding or "utf-8", errors="replace")
            if rewrite_enabled:
                text = rewrite_camera_urls(text, pcfg, printer_id)
            if "text/html" in ctype:
                base = f"/portal-proxy/{pcfg.id}/"
                if "<base " not in text.lower():
                    text = text.replace("<head>", f'<head><base href="{base}">', 1)
                shim = f'<script src="/elegoo/cc2dash-camera-shim.js?printer={pcfg.id}&ip={pcfg.host}"></script>'
                if "cc2dash-camera-shim.js" not in text:
                    text = text.replace("</head>", shim + "</head>", 1)
                resp_headers["content-type"] = "text/html; charset=utf-8"
            content = text.encode("utf-8")
            resp_headers.pop("content-length", None)
        except Exception:
            pass
    return StreamingResponse(iter([content]), status_code=r.status_code, headers=resp_headers, media_type=resp_headers.get("content-type"))


@app.get("/api/logs")
async def api_logs(limit: int = 120, source: Optional[str] = None, level: Optional[str] = None, q: Optional[str] = None):
    return {"ok": True, "logs": get_logs(limit, source=source, level=level, q=q), "sources": log_sources()}


@app.post("/api/setup/finish")
async def api_setup_finish():
    cfg = load_config()
    cfg.setdefault("app", {})["setup_complete"] = True
    save_config(cfg)
    runtime.reload()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    uvicorn.run(
        "cc2_dash.main:app",
        host=cfg.get("app", {}).get("bind_host", "0.0.0.0"),
        port=int(cfg.get("app", {}).get("port", 8088)),
        reload=False,
    )
