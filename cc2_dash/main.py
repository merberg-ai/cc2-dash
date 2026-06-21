from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import ipaddress
import json
import re
import shutil
import sqlite3
import tempfile
import time
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

import httpx
import requests
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

try:
    from PIL import Image
except Exception:  # Pillow is installed for normal use, but keep startup resilient.
    Image = None  # type: ignore[assignment]

from . import __version__
from .config import (
    APP_ROOT,
    DATA_DIR,
    default_printer,
    experimental_feature_locks,
    is_feature_locked,
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
    HOME_AXES,
    MOVE_AXES,
    SET_FAN_SPEED,
    SET_TEMPERATURE,
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
    delete_file_params_legacy,
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
    fan_params,
    filament_info_params,
    filament_motion_params,
    mono_filament_info_params,
    light_params,
    home_axes_params,
    method_allowed,
    move_axes_params,
    print_speed_params,
    print_speed_pct_params,
    temperature_params,
    webcam_params,
)
# Import CommandError from the client module explicitly for clarity.
from .cc2.client import CommandError
from .cc2.discovery import discover
from .cc2.runtime import Cc2PrinterRuntime
from .cc2.state import seconds_to_hms
from .ai import portal_ai
from . import ai_learning
from . import ai_learning_db
from .build_info import get_build_info
from .camera_proxy import camera_proxy_config, camera_relays, rewrite_camera_urls
from .feedback_learning import (
    current_suppressions,
    feedback_stats,
    interpret_feedback,
    record_feedback_suppression,
)
from .vision import vision_monitor
from .print_state import (
    IDLE_MACHINE_STATUS_CODES,
    IDLE_SUB_STATUS_CODES,
    print_phase_from_status as _shared_print_phase_from_status,
    status_looks_active_print as _shared_status_looks_active_print,
    status_is_safe_to_pause as _shared_status_is_safe_to_pause,
)

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
_AI_AUTO_PAUSE_PENDING: dict[str, dict[str, Any]] = {}
_AI_AUTO_PAUSE_CANCELLED: dict[str, float] = {}
_AI_AUTO_PAUSE_LAST_SENT: dict[str, float] = {}
_TIMELAPSE_EXPORT_JOBS: dict[str, dict[str, Any]] = {}
_TIMELAPSE_EXPORT_LOCK = threading.Lock()
_TIMELAPSE_EXPORT_JOB_TTL_SEC = 6 * 60 * 60
_TIMELAPSE_EXPORT_TIMEOUT_SEC = 30 * 60
_LAYER_TOTAL_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_LAYER_TOTAL_CACHE_TTL_SEC = 300.0
_LAYER_TOTAL_MISS_TTL_SEC = 90.0

SPEED_PRESETS = {
    0: "Silent",
    1: "Balanced",
    2: "Sport",
    3: "Ludicrous",
}


CONNECTION_STALE_AFTER_SEC = 35.0
CONNECTION_OFFLINE_AFTER_SEC = 75.0
CONNECTION_MESSAGE_STALE_AFTER_SEC = 90.0


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _coerce_optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _has_real_file(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"-", "none", "None", "null"})


def _print_phase_from_status(status: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> dict[str, Any]:
    return _shared_print_phase_from_status(status, snap)


def _status_looks_active_print(status: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> bool:
    return _shared_status_looks_active_print(status, snap)


def _connection_health_from_snapshot(snap: dict[str, Any] | None) -> dict[str, Any]:
    """Classify printer connection health separately from printer job state.

    A disconnected/stale MQTT client can still have old normalized telemetry in
    memory. This helper is the single source of truth for deciding whether the UI
    should show Online, Stale, Offline, or Auth Error instead of repeating stale
    job states like Printing or Idle.
    """
    if not isinstance(snap, dict) or not snap:
        return {
            "connection_state": "offline",
            "label": "Offline",
            "reachable": False,
            "online": False,
            "offline": True,
            "stale": False,
            "reason": "CC2 MQTT client is not running.",
            "last_message_age_sec": None,
            "last_pong_age_sec": None,
            "stale_after_sec": CONNECTION_STALE_AFTER_SEC,
            "offline_after_sec": CONNECTION_OFFLINE_AFTER_SEC,
        }

    connected = bool(snap.get("connected"))
    registered = bool(snap.get("registered"))
    registration_error = snap.get("registration_error")
    last_error = str(snap.get("last_error") or "").strip()
    last_message_age = _coerce_optional_float(snap.get("last_message_age_sec"))
    last_pong_age = _coerce_optional_float(snap.get("last_pong_age_sec"))

    base = {
        "last_message_age_sec": last_message_age,
        "last_pong_age_sec": last_pong_age,
        "stale_after_sec": CONNECTION_STALE_AFTER_SEC,
        "offline_after_sec": CONNECTION_OFFLINE_AFTER_SEC,
    }

    if registration_error:
        return {
            **base,
            "connection_state": "auth_error",
            "label": "Registration Error",
            "reachable": False,
            "online": False,
            "offline": True,
            "stale": False,
            "reason": f"Printer registration failed: {registration_error}",
        }

    if not connected:
        return {
            **base,
            "connection_state": "offline",
            "label": "Offline",
            "reachable": False,
            "online": False,
            "offline": True,
            "stale": False,
            "reason": last_error or "MQTT connection is closed.",
        }

    if not registered:
        return {
            **base,
            "connection_state": "connecting",
            "label": "Connecting",
            "reachable": False,
            "online": False,
            "offline": False,
            "stale": True,
            "reason": last_error or "MQTT is connected, but printer registration is not confirmed yet.",
        }

    # Prefer the explicit PING/PONG heartbeat. Normal idle printers may not emit
    # rich status changes often, but a fresh PONG means the telemetry path is alive.
    if last_pong_age is not None:
        if last_pong_age > CONNECTION_OFFLINE_AFTER_SEC:
            return {
                **base,
                "connection_state": "offline",
                "label": "Offline",
                "reachable": False,
                "online": False,
                "offline": True,
                "stale": True,
                "reason": f"Printer heartbeat timed out ({int(last_pong_age)}s since last PONG).",
            }
        if last_pong_age > CONNECTION_STALE_AFTER_SEC:
            return {
                **base,
                "connection_state": "stale",
                "label": "Connection Stale",
                "reachable": False,
                "online": False,
                "offline": False,
                "stale": True,
                "reason": f"Printer heartbeat is stale ({int(last_pong_age)}s since last PONG).",
            }
        return {
            **base,
            "connection_state": "online",
            "label": "Online",
            "reachable": True,
            "online": True,
            "offline": False,
            "stale": False,
            "reason": "MQTT is registered and heartbeat is fresh.",
        }

    # Older/partial snapshots may not have PONG age. Fall back to last MQTT
    # message age, but use a slightly looser threshold to avoid idle false alarms.
    if last_message_age is not None:
        if last_message_age > CONNECTION_OFFLINE_AFTER_SEC:
            return {
                **base,
                "connection_state": "offline",
                "label": "Offline",
                "reachable": False,
                "online": False,
                "offline": True,
                "stale": True,
                "reason": f"No printer telemetry for {int(last_message_age)}s.",
            }
        if last_message_age > CONNECTION_STALE_AFTER_SEC:
            return {
                **base,
                "connection_state": "stale",
                "label": "Connection Stale",
                "reachable": False,
                "online": False,
                "offline": False,
                "stale": True,
                "reason": f"Printer telemetry is stale ({int(last_message_age)}s old).",
            }

    return {
        **base,
        "connection_state": "online",
        "label": "Online",
        "reachable": True,
        "online": True,
        "offline": False,
        "stale": False,
        "reason": "MQTT is connected and registered.",
    }


def _connection_status_label(health: dict[str, Any] | None) -> str:
    return str((health or {}).get("label") or "Offline")


def _offline_vision_result(printer_id: str, status: dict[str, Any], source: str = "request") -> dict[str, Any]:
    now = time.time()
    label = str(status.get("status_text") or status.get("connection_state") or "Offline")
    result = {
        "enabled": True,
        "skipped": True,
        "visual_state": "offline",
        "summary": f"Printer is {label}; vision monitoring is paused until telemetry reconnects.",
        "consecutive_bad": 0,
        "last_check_epoch": now,
        "last_check": time.strftime("%H:%M:%S"),
        "source": source,
        "active_print": False,
        "connection_state": status.get("connection_state"),
    }
    return vision_monitor.set_cached_result(printer_id, result)


def _offline_ai_result(printer_id: str, status: dict[str, Any], cfg: dict[str, Any], source: str = "request") -> dict[str, Any]:
    ai_cfg = cfg.get("portal_ai", {}) or {}
    now = time.time()
    label = str(status.get("status_text") or "Offline")
    reason = str(status.get("connection_reason") or status.get("message") or "Printer telemetry is disconnected.")
    vision = status.get("vision_ai") if isinstance(status.get("vision_ai"), dict) else None
    result = {
        "enabled": bool(ai_cfg.get("enabled", True)),
        "state": "printer_offline",
        "level": "watch",
        "risk": 0,
        "summary": label,
        "reasons": [reason, "Failure Detection and auto-pause are paused until the printer telemetry reconnects."],
        "positives": [],
        "active_print": False,
        "monitor_active_prints_only": bool(ai_cfg.get("monitor_active_prints_only", True)),
        "last_check_epoch": now,
        "last_check": time.strftime("%H:%M:%S"),
        "source": source,
        "background_monitor_enabled": bool(ai_cfg.get("background_monitor_enabled", True)),
        "connection_state": status.get("connection_state"),
        "rules": {
            "telemetry": bool(ai_cfg.get("telemetry_rules_enabled", True)),
            "camera": bool(ai_cfg.get("camera_rules_enabled", True)),
            "vision": bool(ai_cfg.get("vision_ai_enabled", False)),
        },
        "vision": vision,
    }
    return portal_ai.set_cached_result(printer_id, result)


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
    exception_summary = str(status.get("exception_summary") or "").strip()
    exception_active = bool(status.get("exceptions") or status.get("exception_details") or exception_summary)
    result = {
        "enabled": bool(ai_cfg.get("enabled", True)),
        "state": "printer_exception" if exception_active else "idle_standby",
        "level": "watch" if exception_active else "low",
        "risk": 45 if exception_active else 0,
        "summary": "Printer Exception" if exception_active else "Idle",
        "reasons": [f"Printer reported exception status: {exception_summary or status.get('exceptions')}.", "Printer is otherwise idle; vision monitoring is paused until an active print starts."] if exception_active else ["Printer is idle; AI watchdog and vision monitoring are paused until an active print starts."],
        "positives": [] if exception_active else ["Printer status is idle."],
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


def _prep_vision_result(printer_id: str, status: dict[str, Any], source: str = "request") -> dict[str, Any]:
    now = time.time()
    phase = status.get("print_phase") if isinstance(status.get("print_phase"), dict) else _print_phase_from_status(status)
    label = str(phase.get("label") or "Preparing")
    result = {
        "enabled": True,
        "skipped": True,
        "visual_state": "standby",
        "summary": f"Printer is {label}; vision failure checks are paused until actual printing begins.",
        "consecutive_bad": 0,
        "last_check_epoch": now,
        "last_check": time.strftime("%H:%M:%S"),
        "source": source,
        "active_print": bool(status.get("active_print")),
        "print_phase": phase,
    }
    return vision_monitor.set_cached_result(printer_id, result)


def _prep_ai_result(printer_id: str, status: dict[str, Any], cfg: dict[str, Any], source: str = "request") -> dict[str, Any]:
    ai_cfg = cfg.get("portal_ai", {}) or {}
    now = time.time()
    phase = status.get("print_phase") if isinstance(status.get("print_phase"), dict) else _print_phase_from_status(status)
    label = str(phase.get("label") or "Preparing")
    vision = status.get("vision_ai") if isinstance(status.get("vision_ai"), dict) else None
    exception_summary = str(status.get("exception_summary") or "").strip()
    exception_active = bool(status.get("exceptions") or status.get("exception_details") or exception_summary)
    result = {
        "enabled": bool(ai_cfg.get("enabled", True)),
        "state": "printer_exception" if exception_active else "preparing",
        "level": "watch" if exception_active else "low",
        "risk": 45 if exception_active else 0,
        "summary": "Printer Exception" if exception_active else "Preparing",
        "reasons": [
            f"Printer reported exception status: {exception_summary or status.get('exceptions')}.",
            f"Printer is also in a start-of-job state: {label}.",
        ] if exception_active else [
            f"Printer is in a normal start-of-job state: {label}.",
            "Failure alerts are paused for preheating/homing/leveling and will resume when printing starts.",
        ],
        "positives": [] if exception_active else ["Telemetry state says the printer is preparing the job, not failing it."],
        "active_print": bool(status.get("active_print")),
        "monitor_active_prints_only": bool(ai_cfg.get("monitor_active_prints_only", True)),
        "print_phase": phase,
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


def _nice_status(value: Any, fallback: str = "Unknown") -> str:
    raw = str(value or "").replace("_", " ").strip()
    if not raw:
        return fallback
    if raw.isupper() or raw.islower():
        return raw.title()
    return raw


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
        # Firmware sometimes reports the current layer but not total layers.
        # Show that honestly as current/? instead of making it look like
        # we intentionally chose the single-layer display.
        return f"{cur}/?"
    if tot is not None and tot > 0:
        return f"-/{tot}"
    return "-"


def _positive_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        number = int(float(value))
        return number if number > 0 else None
    except Exception:
        return None


def _same_file_name(left: Any, right: Any) -> bool:
    a = _basename_from_path(left).lower()
    b = _basename_from_path(right).lower()
    return bool(a and b and a == b)


def _find_total_layer_in_payload(payload: Any, filename: str | None = None) -> tuple[int | None, str | None]:
    """Find a stock-portal-style total layer value in file-list/detail data.

    The live printer status currently reports print_status.current_layer but not
    total layers. The stock Elegoo portal gets total layers from cached file list
    rows or from the file-detail command, where the field may be named Layer,
    TotalLayer, or TotalLayers depending on firmware/build. Keep this helper
    scoped to file metadata payloads so a generic ``Layer`` field is interpreted
    as total layers, not the live current layer.
    """
    wanted = str(filename or "").strip()
    total_keys = {
        "totallayers", "total_layers", "totallayer", "total_layer",
        "layercount", "layer_count", "layer", "layers",
    }
    name_keys = {"filename", "file_name", "filename", "name", "taskname", "task_name", "filepath", "file_path", "path"}

    def record_name(d: dict[str, Any]) -> str:
        for key, value in d.items():
            if str(key).replace("-", "_").lower() in name_keys and value not in (None, ""):
                return str(value)
        return ""

    def scan(obj: Any, path: str = "") -> tuple[int | None, str | None]:
        if isinstance(obj, dict):
            # Prefer an object that explicitly belongs to the current filename.
            if wanted:
                rec = record_name(obj)
                if rec and not _same_file_name(rec, wanted):
                    # Still scan nested containers; just don't use this row's own
                    # scalar fields as the answer for a different file.
                    for key, value in obj.items():
                        if isinstance(value, (dict, list)):
                            found, src = scan(value, f"{path}.{key}" if path else str(key))
                            if found is not None:
                                return found, src
                    return None, None
            for key, value in obj.items():
                norm = str(key).replace("-", "_").lower()
                if norm in total_keys:
                    number = _positive_int(value)
                    if number is not None:
                        return number, f"file_metadata.{path + '.' if path else ''}{key}"
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    found, src = scan(value, f"{path}.{key}" if path else str(key))
                    if found is not None:
                        return found, src
        elif isinstance(obj, list):
            # If filename is provided, prioritize matching file rows.
            if wanted:
                for idx, item in enumerate(obj):
                    if isinstance(item, dict) and _same_file_name(record_name(item), wanted):
                        found, src = scan(item, f"{path}[{idx}]")
                        if found is not None:
                            return found, src
            for idx, item in enumerate(obj):
                found, src = scan(item, f"{path}[{idx}]")
                if found is not None:
                    return found, src
        return None, None

    root = _unwrap_command_payload(payload)
    return scan(root)


def _cache_layer_total(printer_id: str, filename: str, storage_media: str, total: int | None, source: str | None) -> None:
    key = (str(printer_id), _basename_from_path(filename).lower(), normalize_storage_media(storage_media))
    _LAYER_TOTAL_CACHE[key] = {
        "time": time.time(),
        "total": total,
        "source": source,
    }


def _cached_layer_total(printer_id: str, filename: str, storage_media: str) -> tuple[bool, int | None, str | None]:
    key = (str(printer_id), _basename_from_path(filename).lower(), normalize_storage_media(storage_media))
    row = _LAYER_TOTAL_CACHE.get(key)
    if not row:
        return False, None, None
    age = time.time() - float(row.get("time") or 0)
    ttl = _LAYER_TOTAL_CACHE_TTL_SEC if row.get("total") else _LAYER_TOTAL_MISS_TTL_SEC
    if age > ttl:
        _LAYER_TOTAL_CACHE.pop(key, None)
        return False, None, None
    return True, row.get("total"), row.get("source")


def _lookup_total_layer_from_file_metadata_sync(printer_id: str, filename: str, storage_media: str = "local") -> tuple[int | None, str | None]:
    if not _has_real_file(filename):
        return None, None
    media = normalize_storage_media(storage_media)
    cached, total, source = _cached_layer_total(printer_id, filename, media)
    if cached:
        return total, source

    # This mirrors the stock Elegoo portal: when live status lacks total layers,
    # ask the file-detail/file-list side for metadata about the currently printed file.
    lookups: list[tuple[str, int, dict[str, Any], float]] = [
        ("detail", GET_FILE_DETAIL, file_detail_params(filename, media), 15.0),
        ("list", GET_FILE_LIST, file_list_params("/", media, page=1, page_size=200), 15.0),
    ]
    # USB prints may show up with a leading slash in file APIs; try both common
    # media buckets without making every dashboard refresh hammer the printer.
    if media != "u-disk":
        lookups.extend([
            ("udisk_detail", GET_FILE_DETAIL, file_detail_params(filename, "u-disk"), 15.0),
            ("udisk_list", GET_FILE_LIST, file_list_params("/", "u-disk", page=1, page_size=200), 15.0),
        ])

    for label, method, params, timeout in lookups:
        try:
            payload = _send_command(printer_id, method, params, True, timeout, False)
        except Exception as exc:
            log("debug", f"Layer-total {label} lookup failed for {filename}: {exc}", "command", printer=printer_id)
            continue
        total, source = _find_total_layer_in_payload(payload, filename)
        if total is not None:
            src = f"{label}.{source or 'unknown'}"
            _cache_layer_total(printer_id, filename, media, total, src)
            return total, src
    _cache_layer_total(printer_id, filename, media, None, "file_metadata.miss")
    return None, None


async def _enrich_status_with_file_layer_total(printer_id: str, status: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict):
        return status
    if not status.get("layer_total_missing"):
        return status
    if not status.get("reachable"):
        return status
    filename = status.get("file")
    if not _has_real_file(filename):
        return status
    try:
        total, source = await asyncio.to_thread(_lookup_total_layer_from_file_metadata_sync, printer_id, str(filename), "local")
    except Exception as exc:
        log("debug", f"Layer-total metadata lookup failed for {filename}: {exc}", "command", printer=printer_id)
        return status
    if total is None:
        return status
    status["layer_total"] = total
    status["layer_progress"] = _format_layer_progress(status.get("layer_current"), total)
    status["layer_total_missing"] = False
    status["layer_total_from_file_metadata"] = True
    status["layer_total_source"] = source
    src = status.get("layer_source") if isinstance(status.get("layer_source"), dict) else {}
    src["total"] = source
    status["layer_source"] = src
    return status


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
        "layer_total_missing": current_layer not in (None, "") and total_layer in (None, ""),
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


class ControlFanRequest(BaseModel):
    fan: str
    percent: int = Field(ge=0, le=100)


class ControlSpeedRequest(BaseModel):
    percent: int = Field(ge=1, le=300)


class ControlTemperatureRequest(BaseModel):
    tool: str
    target: int = Field(ge=0, le=350)


class ControlMoveRequest(BaseModel):
    axis: str
    step: float = Field(gt=-301, lt=301)


class ControlHomeRequest(BaseModel):
    axis: str = "XYZ"


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


class StagedUploadSendRequest(BaseModel):
    storage_media: str = "local"
    print_after: bool = False
    start_layer: int = 0
    calibration: bool = False
    platform_type: int = 0
    timelapse: bool = False


class TimelapseExportRequest(BaseModel):
    url: str
    task_id: str | int | None = None
    task_name: str | None = None


class HistoryDeleteRequest(BaseModel):
    task_ids: list[str | int]


class SaveConfigRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class AIFeedbackRequest(BaseModel):
    label: str
    note: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    annotation: dict[str, Any] = Field(default_factory=dict)


class AIFeedbackFrameRequest(BaseModel):
    label: str = "missed_failure"
    context: dict[str, Any] = Field(default_factory=dict)


class AIFeedbackReasonRequest(BaseModel):
    sample_id: Optional[int] = None
    feedback_timestamp: Optional[float] = None
    label: str = ""
    reason: str
    reason_key: str = ""


class AIEnabledRequest(BaseModel):
    enabled: bool


class AutoPauseCancelRequest(BaseModel):
    token: Optional[str] = None
    reason: str = ""


class AutoPauseNowRequest(BaseModel):
    token: Optional[str] = None


class LearningResetRequest(BaseModel):
    delete_samples: bool = False


class LearningImportRequest(BaseModel):
    rebuild_profiles: bool = True
    limit: Optional[int] = None


class LearningSampleReviewRequest(BaseModel):
    feedback_label: Optional[str] = None
    outcome: Optional[str] = None
    feedback_note: Optional[str] = None
    reason_key: Optional[str] = None
    rebuild_profile: bool = True


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


def _cleanup_auto_pause_state(now: float | None = None) -> None:
    now = now or time.time()
    for token, expiry in list(_AI_AUTO_PAUSE_CANCELLED.items()):
        if float(expiry or 0) <= now:
            _AI_AUTO_PAUSE_CANCELLED.pop(token, None)


def _auto_pause_signature(printer_id: str, status: dict[str, Any], result: dict[str, Any]) -> str:
    reason = str((result.get("reasons") or [""])[0] or "")[:180]
    bucket = int((_coerce_float(status.get("progress"), 0.0) // 5) * 5)
    raw = "|".join([
        str(printer_id),
        str(status.get("file") or "-"),
        str(status.get("status_text") or status.get("state") or ""),
        str(result.get("state") or ""),
        str(result.get("level") or ""),
        str(bucket),
        reason,
    ])
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


def _auto_pause_failure_type(result: dict[str, Any]) -> str:
    text = " ".join(str(x or "") for x in (result.get("reasons") or [])).lower()
    if any(w in text for w in ("filament", "runout", "no filament")):
        return "Filament / runout warning"
    if any(w in text for w in ("hotend", "bed", "temperature", "target", "below")):
        return "Temperature warning"
    if any(w in text for w in ("spaghetti", "stringing", "detached", "blob", "vision", "camera image", "ollama")):
        return "Vision failure warning"
    if any(w in text for w in ("stale", "mqtt", "telemetry", "reachable", "registered")):
        return "Telemetry warning"
    if "camera" in text:
        return "Camera warning"
    return "High-risk failure warning"


def _auto_pause_vision_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    for key in ("vision", "vision_ai"):
        value = result.get(key)
        if isinstance(value, dict):
            return value
    return None


def _auto_pause_failure_family(result: dict[str, Any]) -> str:
    """Stable-ish bucket used to verify that a fresh recheck sees the same failure type."""
    vision = _auto_pause_vision_result(result)
    text = " ".join(str(x or "") for x in (result.get("reasons") or [])).lower()
    if isinstance(vision, dict):
        visual_state = str(vision.get("visual_state") or "").lower()
        failure_types = [str(x or "").strip().lower() for x in (vision.get("failure_types") or []) if str(x or "").strip()]
        if visual_state == "camera_bad":
            return "camera_quality"
        if visual_state in {"possible_failure", "failure_likely"} or any(w in text for w in ("spaghetti", "stringing", "detached", "blob", "ollama vision")):
            first = failure_types[0] if failure_types else "unknown"
            return f"vision:{first}"
    if any(w in text for w in ("filament", "runout", "no filament")):
        return "filament"
    if any(w in text for w in ("hotend", "bed", "temperature", "target", "below")):
        return "temperature"
    if any(w in text for w in ("progress", "unchanged", "stuck")):
        return "progress"
    if any(w in text for w in ("stale", "mqtt", "telemetry", "reachable", "registered")):
        return "telemetry"
    if "camera" in text:
        return "camera_quality"
    return "general"


def _auto_pause_progress_bucket(status: dict[str, Any]) -> int | None:
    try:
        return int((_coerce_float(status.get("progress"), 0.0) // 5) * 5)
    except Exception:
        return None


def _auto_pause_permission_gate(
    printer_id: str,
    status: dict[str, Any],
    result: dict[str, Any],
    cfg: dict[str, Any],
    pending: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single source of truth for whether Failure Detection may send PAUSE_PRINT.

    This gate is intentionally stricter than the dashboard warning level. Warnings can
    be noisy; pause permission should be boring, explainable, and reversible. Cancel
    permission stays locked out here on purpose.
    """
    ai_cfg = cfg.get("portal_ai", {}) or {}
    threshold = max(50, min(100, int(_coerce_float(ai_cfg.get("auto_pause_threshold"), 90))))
    require_high = bool(ai_cfg.get("auto_pause_require_high_level", True))
    enabled = bool(ai_cfg.get("enabled", True)) and bool(ai_cfg.get("auto_pause_enabled", False))
    out = result or {}
    risk = int(_coerce_float(out.get("risk"), 0.0))
    level = str(out.get("level") or "low").lower()
    state = str(out.get("state") or "").lower()
    family = _auto_pause_failure_family(out)
    vision = _auto_pause_vision_result(out)
    safe_to_pause, safety_reason = _status_is_safe_to_pause(status)

    vetoes: list[str] = []
    evidence: list[str] = []
    if not enabled:
        vetoes.append("auto-pause is disabled")
    if out.get("enabled", True) is False:
        vetoes.append("Failure Detection is disabled for this result")
    if not safe_to_pause:
        vetoes.append(safety_reason)
    else:
        evidence.append("printer state is pause-eligible")
    if risk < threshold:
        vetoes.append(f"risk {risk}% is below the auto-pause threshold {threshold}%")
    else:
        evidence.append(f"risk {risk}% meets threshold {threshold}%")
    if require_high and not (level == "high" or state == "failure_likely"):
        vetoes.append("result is not high/failure-likely")
    elif require_high:
        evidence.append("result is high/failure-likely")

    connection_state = str(status.get("connection_state") or "online").lower()
    if status.get("offline") or status.get("stale") or connection_state not in {"", "online"}:
        vetoes.append(f"printer telemetry is {connection_state or 'not online'}")

    if family == "telemetry":
        vetoes.append("telemetry warning alone is not pause-grade")
    if family == "camera_quality":
        vetoes.append("camera/view quality alone only allows inspect, not auto-pause")

    if isinstance(vision, dict) and family.startswith("vision:"):
        visual_state = str(vision.get("visual_state") or "").lower()
        recommended_action = str(vision.get("recommended_action") or "").lower()
        if vision.get("feedback_suppressed"):
            vetoes.append("similar vision result was suppressed by feedback learning")
        if vision.get("benign_uncertainty") or vision.get("normalized_from") == "uncertain":
            vetoes.append("vision result was normalized to benign uncertainty")
        if visual_state not in {"possible_failure", "failure_likely"}:
            vetoes.append(f"vision state {visual_state or 'unknown'} is not a pause-grade failure")
        if not vision.get("bad_confirmed"):
            bad = int(_coerce_float(vision.get("consecutive_bad"), 0.0))
            required = int(_coerce_float(vision.get("required_bad_checks"), 2.0))
            vetoes.append(f"vision has not confirmed repeated bad frames ({bad}/{required})")
        if recommended_action in {"keep_watching", "inspect"} and visual_state != "failure_likely":
            vetoes.append(f"vision recommended {recommended_action}, not pause_print")
        if not vetoes:
            bad = int(_coerce_float(vision.get("consecutive_bad"), 0.0))
            required = int(_coerce_float(vision.get("required_bad_checks"), 2.0))
            evidence.append(f"vision confirmed {visual_state.replace('_', ' ')} over {bad}/{required} bad checks")

    if pending:
        pending_family = str(pending.get("failure_family") or "")
        if pending_family and pending_family != family:
            vetoes.append(f"fresh recheck reported a different failure family ({family}, was {pending_family})")
        pending_file = str(pending.get("file") or "").strip()
        current_file = str(status.get("file") or "").strip()
        if pending_file and current_file and pending_file != current_file:
            vetoes.append("fresh recheck is for a different print file")
        pending_bucket = pending.get("progress_bucket")
        current_bucket = _auto_pause_progress_bucket(status)
        if isinstance(pending_bucket, int) and isinstance(current_bucket, int) and abs(current_bucket - pending_bucket) > 10:
            vetoes.append("fresh recheck is too far from the original progress window")

    pause_allowed = not vetoes
    reason = "pause permitted" if pause_allowed else vetoes[0]
    return {
        "pause_allowed": pause_allowed,
        "cancel_allowed": False,
        "warn_allowed": True,
        "allowed_actions": {"warn": True, "pause": pause_allowed, "cancel": False},
        "reason": reason,
        "vetoes": vetoes[:8],
        "evidence": evidence[:8],
        "failure_family": family,
        "requires_fresh_recheck": True,
        "threshold": threshold,
        "require_high_level": require_high,
    }


def _auto_pause_fresh_recheck(printer_id: str, cfg: dict[str, Any], pending: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a fresh status + AI result immediately before a pause command is sent."""
    printer = (cfg.get("printers") or {}).get(printer_id)
    if not printer:
        return {"ok": False, "error": "Printer not configured"}
    try:
        if not runtime.get_client(printer_id):
            runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
        snap = runtime.snapshot(printer_id)
        status = _status_from_snapshot(
            printer_id,
            printer,
            snap,
            ai_source="auto_pause_recheck",
            force_ai_evaluate=False,
            attach_ai=False,
        )
        # Force a fresh vision frame before acting. If vision is disabled or the
        # printer is no longer active, _maybe_attach_vision will safely stand down.
        status = _maybe_attach_vision(printer_id, printer, status, cfg, ai_source="background", force=True)
        result = portal_ai.evaluate(printer_id, status, snap, cfg, source="auto_pause_recheck")
        status["portal_ai"] = result
        gate = _auto_pause_permission_gate(printer_id, status, result, cfg, pending=pending)
        return {"ok": True, "status": status, "result": result, "gate": gate}
    except Exception as exc:
        return {"ok": False, "error": str(getattr(exc, "detail", None) or exc)}


def _status_is_safe_to_pause(status: dict[str, Any]) -> tuple[bool, str]:
    return _shared_status_is_safe_to_pause(status)


def _auto_pause_public_pending(pending: dict[str, Any] | None, now: float | None = None) -> dict[str, Any] | None:
    if not pending:
        return None
    now = now or time.time()
    deadline = float(pending.get("deadline_epoch") or 0)
    return {
        "token": pending.get("token"),
        "created_epoch": pending.get("created_epoch"),
        "deadline_epoch": deadline,
        "remaining_seconds": max(0, int(round(deadline - now))),
        "countdown_seconds": pending.get("countdown_seconds"),
        "risk": pending.get("risk"),
        "level": pending.get("level"),
        "failure_type": pending.get("failure_type"),
        "failure_family": pending.get("failure_family"),
        "message": pending.get("message"),
        "reason": pending.get("reason"),
        "permission": pending.get("permission"),
    }


def _process_auto_pause(printer_id: str, status: dict[str, Any], result: dict[str, Any], cfg: dict[str, Any], schedule: bool = False) -> dict[str, Any]:
    """Attach auto-pause metadata and, from the background watchdog only, execute due pauses.

    The dashboard owns the human-facing countdown modal, but the backend owns the
    pending timer so the action is not just a browser-side toy. Every pause command
    now passes through one permission gate and a fresh recheck immediately before
    PAUSE_PRINT is sent. Cancel remains manual-only by design.
    """
    ai_cfg = cfg.get("portal_ai", {}) or {}
    now = time.time()
    _cleanup_auto_pause_state(now)
    out = dict(result or {})
    threshold = max(50, min(100, int(_coerce_float(ai_cfg.get("auto_pause_threshold"), 90))))
    countdown = max(5, min(600, int(_coerce_float(ai_cfg.get("auto_pause_countdown_seconds"), 30))))
    cooldown_minutes = max(1.0, min(240.0, _coerce_float(ai_cfg.get("auto_pause_cooldown_minutes"), 10.0)))
    enabled = bool(ai_cfg.get("enabled", True)) and bool(ai_cfg.get("auto_pause_enabled", False))
    risk = int(_coerce_float(out.get("risk"), 0.0))
    level = str(out.get("level") or "low").lower()
    token = _auto_pause_signature(printer_id, status, out)
    failure_type = _auto_pause_failure_type(out)
    reason = str((out.get("reasons") or ["Failure Detection reported a high-risk condition."])[0] or "Failure Detection reported a high-risk condition.")
    gate = _auto_pause_permission_gate(printer_id, status, out, cfg)
    safety_reason = str(gate.get("reason") or "standing by")
    failure_family = str(gate.get("failure_family") or _auto_pause_failure_family(out))
    eligible = bool(gate.get("pause_allowed"))

    pending = _AI_AUTO_PAUSE_PENDING.get(printer_id)
    if pending and (not eligible or pending.get("token") != token):
        _AI_AUTO_PAUSE_PENDING.pop(printer_id, None)
        pending = None

    cancelled_until = float(_AI_AUTO_PAUSE_CANCELLED.get(token) or 0)
    if cancelled_until > now:
        eligible = False
        safety_reason = f"auto-pause was cancelled for this failure signature for {int((cancelled_until - now) // 60) + 1} more minute(s)"

    last_sent = float(_AI_AUTO_PAUSE_LAST_SENT.get(printer_id) or 0)
    cooldown_remaining = max(0, int(round((last_sent + cooldown_minutes * 60.0) - now)))
    if cooldown_remaining > 0:
        eligible = False
        safety_reason = f"auto-pause cooldown active for {cooldown_remaining}s"

    sent = None
    error = None
    recheck_public = None
    if pending and eligible and schedule and now >= float(pending.get("deadline_epoch") or 0):
        recheck = _auto_pause_fresh_recheck(printer_id, cfg, pending=pending)
        recheck_gate = recheck.get("gate") if isinstance(recheck.get("gate"), dict) else None
        recheck_public = {
            "ok": bool(recheck.get("ok")),
            "reason": (recheck_gate or {}).get("reason") or recheck.get("error"),
            "allowed": bool((recheck_gate or {}).get("pause_allowed")),
            "failure_family": (recheck_gate or {}).get("failure_family"),
            "vetoes": (recheck_gate or {}).get("vetoes", []),
        }
        if not recheck.get("ok") or not (recheck_gate or {}).get("pause_allowed"):
            error = str(recheck_public.get("reason") or "fresh recheck did not permit auto-pause")
            pending["last_error"] = error
            _AI_AUTO_PAUSE_PENDING.pop(printer_id, None)
            pending = None
            _AI_AUTO_PAUSE_CANCELLED[token] = now + min(180.0, cooldown_minutes * 60.0)
            log("warning", f"Failure Detection auto-pause blocked by fresh recheck: {error}", "portal_ai", printer=printer_id)
        else:
            try:
                _send_command(printer_id, PAUSE_PRINT, {}, True, 60.0, True)
                sent = {"ok": True, "sent_epoch": now, "message": "Pause command sent by Failure Detection after a fresh safety recheck."}
                _AI_AUTO_PAUSE_LAST_SENT[printer_id] = now
                _AI_AUTO_PAUSE_PENDING.pop(printer_id, None)
                pending = None
                log("warning", f"Failure Detection auto-pause sent: {failure_type} ({risk}%). {reason}", "portal_ai", printer=printer_id)
            except Exception as exc:
                error = str(getattr(exc, "detail", None) or exc)
                pending["last_error"] = error
                # Avoid hammering the printer every watchdog tick after one failed attempt.
                _AI_AUTO_PAUSE_CANCELLED[token] = now + min(300.0, cooldown_minutes * 60.0)
                log("error", f"Failure Detection auto-pause failed: {error}", "portal_ai", printer=printer_id)

    if not pending and eligible and schedule and sent is None and error is None:
        pending = {
            "token": token,
            "created_epoch": now,
            "deadline_epoch": now + countdown,
            "countdown_seconds": countdown,
            "risk": risk,
            "level": level,
            "failure_type": failure_type,
            "failure_family": failure_family,
            "file": str(status.get("file") or "").strip(),
            "progress_bucket": _auto_pause_progress_bucket(status),
            "message": f"Failure Detection may pause this print in {countdown} seconds unless you cancel.",
            "reason": reason,
            "permission": gate,
        }
        _AI_AUTO_PAUSE_PENDING[printer_id] = pending
        log("warning", f"Failure Detection auto-pause countdown armed: {failure_type} ({risk}%). {reason}", "portal_ai", printer=printer_id)

    pending = _AI_AUTO_PAUSE_PENDING.get(printer_id)
    action_state = "disabled"
    if enabled:
        action_state = "pending" if pending else ("sent" if sent else ("armed" if eligible else "standing_by"))
    auto_pause = {
        "enabled": enabled,
        "threshold": threshold,
        "countdown_seconds": countdown,
        "cooldown_minutes": cooldown_minutes,
        "require_high_level": bool(ai_cfg.get("auto_pause_require_high_level", True)),
        "eligible": eligible,
        "safety_reason": safety_reason,
        "failure_type": failure_type,
        "failure_family": failure_family,
        "reason": reason,
        "permission": gate,
        "allowed_actions": gate.get("allowed_actions"),
        "requires_fresh_recheck": True,
        "state": action_state,
        "pending": _auto_pause_public_pending(pending, now),
        "sent": sent,
        "fresh_recheck": recheck_public,
        "error": error or (pending or {}).get("last_error"),
    }
    out["auto_pause"] = auto_pause
    return out


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


def _printer_match(printer: Optional[str], cfg: dict[str, Any] | None = None) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve a printer identifier/name/host/serial without changing defaults."""
    cfg = cfg or load_config()
    printers = cfg.get("printers") or {}
    wanted = str(printer or "").strip()
    if not wanted:
        return None, None
    if wanted in printers:
        return wanted, printers[wanted]
    lowered = wanted.lower()
    for pid, pdata in printers.items():
        pcfg = printer_dict_to_config(pid, pdata)
        if pcfg.host == wanted or pcfg.name.lower() == lowered or pcfg.serial.lower() == lowered:
            return pid, pdata
    return None, None


def _selected_printer(cfg: dict[str, Any], printer: Optional[str] = None) -> tuple[str | None, dict[str, Any] | None]:
    """Return the UI-selected printer, falling back to the configured default.

    Multi-printer pages should use this as their view context. It deliberately
    does not mutate app.default_printer; a selected/viewed printer is not the
    same thing as the saved default printer.
    """
    pid, pdata = _printer_match(printer, cfg)
    if pid and pdata:
        return pid, pdata
    return default_printer(cfg)


def _portal_target(printer: Optional[str] = None):
    cfg = load_config()
    pid, pdata = _selected_printer(cfg, printer)
    if pid and pdata:
        pcfg = printer_dict_to_config(pid, pdata)
        pcfg.id = pid
        return pcfg
    return None


def _printer_query(pid: str | None) -> str:
    return f"?printer={quote(str(pid))}" if pid else ""


def _path_with_printer(path: str, pid: str | None) -> str:
    return f"{path}{_printer_query(pid)}"


def view_context(request: Request) -> dict[str, Any]:
    cfg = load_config()
    theme = get_theme(cfg.get("app", {}).get("theme"))
    requested_printer = request.query_params.get("printer") if request else None
    pid, printer = _selected_printer(cfg, requested_printer)
    default_pid, _default_printer_data = default_printer(cfg)
    public_printer = None
    if pid and printer:
        public_printer = public_printer_dict(printer_dict_to_config(pid, printer), include_secret=False)
    configured_printers = []
    for configured_pid, pdata in (cfg.get("printers") or {}).items():
        row = public_printer_dict(printer_dict_to_config(configured_pid, pdata), include_secret=False)
        row["selected"] = configured_pid == pid
        row["is_default"] = configured_pid == default_pid
        configured_printers.append(row)
    nav_printer_qs = _printer_query(pid)
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
        "configured_printers": configured_printers,
        "default_printer_id": default_pid,
        "nav_printer_qs": nav_printer_qs,
        "nav_url": lambda path: _path_with_printer(path, pid),
        "default_subnet": default_subnet_guess(),
        "experimental_feature_locks": experimental_feature_locks(),
    }


def _locked_feature_page(request: Request, feature_key: str):
    meta = experimental_feature_locks().get(feature_key, {})
    context = view_context(request)
    context["feature_name"] = meta.get("label", "Feature")
    context["feature_summary"] = meta.get("summary", "This feature is temporarily disabled in this build.")
    context["feature_path"] = meta.get("path", "")
    return templates.TemplateResponse("feature_disabled.html", context, status_code=403)


def _raise_if_feature_locked(feature_key: str) -> None:
    meta = experimental_feature_locks().get(feature_key)
    if meta:
        raise HTTPException(403, f"{meta.get('label', 'Feature')} is temporarily disabled in this community test build.")


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


@app.get("/ai-training", response_class=HTMLResponse)
async def ai_training_page(request: Request):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("ai_training.html", view_context(request))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return templates.TemplateResponse("logs.html", view_context(request))


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("upload.html", view_context(request))


@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    if is_feature_locked("file_manager_enabled"):
        return _locked_feature_page(request, "file_manager_enabled")
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("files.html", view_context(request))


@app.get("/filaments", response_class=HTMLResponse)
async def filaments_page(request: Request):
    if is_feature_locked("filament_manager_enabled"):
        return _locked_feature_page(request, "filament_manager_enabled")
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("filaments.html", view_context(request))


@app.get("/control", response_class=HTMLResponse)
async def control_page(request: Request):
    if is_feature_locked("control_page_enabled"):
        return _locked_feature_page(request, "control_page_enabled")
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("control.html", view_context(request))


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
  <div class="bar"><strong>Elegoo portal</strong><span>{pcfg.name} · {pcfg.host}</span><a href="/?printer={pcfg.id}">Back</a><a href="{fullscreen_url}" target="_blank">Fullscreen</a><a href="{root_url}" target="_blank">Printer root</a><a href="{diag_url}" target="_blank">Probe</a></div>
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
</head><body><div class="bar"><strong>Elegoo live portal</strong><span>{pcfg.name} · MQTT WS bridge · {pcfg.host}:{pcfg.port}</span><a href="/?printer={pcfg.id}">Back</a><a href="{app_url}" target="_blank">Open raw app</a><a href="/api/portal-probe?printer={pcfg.id}" target="_blank">Probe</a></div><iframe src="{app_url}" title="Elegoo live portal"></iframe></body></html>"""
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
    return {"ok": True, "config": load_config(), "themes": THEMES, "font_stacks": list(FONT_STACKS.keys()), "experimental_feature_locks": experimental_feature_locks()}


@app.post("/api/config")
async def api_save_config(req: SaveConfigRequest):
    cfg = save_config(req.config)
    runtime.reload()
    camera_relays.configure_from_config(cfg)
    log("info", "Configuration saved", "settings")
    return {"ok": True, "config": cfg, "experimental_feature_locks": experimental_feature_locks()}


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
    connection_state = str(status.get("connection_state") or "online").lower()
    if status.get("offline") or status.get("stale") or connection_state not in {"", "online"}:
        if ai_cfg.get("vision_ai_enabled", False):
            status["vision_ai"] = _offline_vision_result(printer_id, status, ai_source)
        return status
    phase = status.get("print_phase") if isinstance(status.get("print_phase"), dict) else _print_phase_from_status(status)
    if phase.get("is_preparing"):
        status["print_phase"] = phase
        if ai_cfg.get("vision_ai_enabled", False):
            status["vision_ai"] = _prep_vision_result(printer_id, status, ai_source)
        return status
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
    schedule_auto_pause = ai_source == "background" or (ai_source == "request" and not bool(ai_cfg.get("background_monitor_enabled", True)))
    connection_state = str(status.get("connection_state") or "online").lower()
    if status.get("offline") or status.get("stale") or connection_state not in {"", "online"}:
        if ai_cfg.get("vision_ai_enabled", False):
            status["vision_ai"] = _offline_vision_result(printer_id, status, ai_source)
        result = _offline_ai_result(printer_id, status, cfg, ai_source)
        status["portal_ai"] = _process_auto_pause(printer_id, status, result, cfg, schedule=False)
        return status
    if not ai_cfg.get("enabled", True):
        result = portal_ai.evaluate(printer_id, status, snap, cfg, source=ai_source)
        status["portal_ai"] = _process_auto_pause(printer_id, status, result, cfg, schedule=False)
        return status
    phase = status.get("print_phase") if isinstance(status.get("print_phase"), dict) else _print_phase_from_status(status, snap)
    if phase.get("is_preparing"):
        status["print_phase"] = phase
        if ai_cfg.get("vision_ai_enabled", False):
            status["vision_ai"] = _prep_vision_result(printer_id, status, ai_source)
        result = _prep_ai_result(printer_id, status, cfg, ai_source)
        status["portal_ai"] = _process_auto_pause(printer_id, status, result, cfg, schedule=False)
        return status
    if ai_cfg.get("monitor_active_prints_only", True) and not bool(status.get("active_print")):
        if ai_cfg.get("vision_ai_enabled", False):
            status["vision_ai"] = _idle_vision_result(printer_id, ai_source)
        result = _idle_ai_result(printer_id, status, cfg, ai_source)
        status["portal_ai"] = _process_auto_pause(printer_id, status, result, cfg, schedule=False)
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
            status["portal_ai"] = _process_auto_pause(printer_id, status, cached, cfg, schedule=False)
            return status
    status = _maybe_attach_vision(printer_id, printer, status, cfg, ai_source=ai_source, force=force_ai_evaluate)
    result = portal_ai.evaluate(printer_id, status, snap, cfg, source=ai_source)
    status["portal_ai"] = _process_auto_pause(printer_id, status, result, cfg, schedule=schedule_auto_pause)
    return status

def _status_from_snapshot(printer_id: str, printer: dict[str, Any], snap: Optional[dict[str, Any]], ai_source: str = "request", force_ai_evaluate: bool = False, attach_ai: bool = True) -> dict[str, Any]:
    pcfg = printer_dict_to_config(printer_id, printer)
    if not snap:
        cfg = load_config()
        health = _connection_health_from_snapshot(None)
        status = PrinterClient(printer_id, printer, cfg)._empty_status("CC2 client is not running", reachable=False)
        status.update({
            "connection_state": health["connection_state"],
            "connection_health": health,
            "connection_reason": health["reason"],
            "offline": health["offline"],
            "stale": health["stale"],
            "reachable": health["reachable"],
            "connected": False,
            "registered": False,
            "state": health["connection_state"],
            "status_text": health["label"],
            "message": health["reason"],
            "active_print": False,
            "print_phase": {"is_preparing": False, "kind": "offline", "label": health["label"], "status_code": None, "sub_status_code": None},
        })
        if not attach_ai:
            return status
        return _attach_ai_status(printer_id, status, None, cfg, ai_source=ai_source, force_ai_evaluate=force_ai_evaluate, printer=printer)
    health = _connection_health_from_snapshot(snap)
    n = snap.get("normalized") or {}
    temps = n.get("temps") or {}
    nozzle = temps.get("nozzle") or {}
    bed = temps.get("bed") or {}
    chamber = temps.get("chamber") or {}
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
    reachable = bool(health.get("reachable"))
    if not reachable:
        state = health.get("connection_state") or "offline"
    status = {
        "printer_id": printer_id,
        "name": pcfg.name,
        "host": pcfg.host,
        "serial": pcfg.serial,
        "reachable": reachable,
        "connected": bool(snap.get("connected")),
        "registered": bool(snap.get("registered")),
        "connection_state": health.get("connection_state"),
        "connection_health": health,
        "connection_reason": health.get("reason"),
        "offline": bool(health.get("offline")),
        "stale": bool(health.get("stale")),
        "state": str(state).lower(),
        "status_text": str(health.get("label") if not reachable else state).replace("_", " ").title(),
        "status_code": n.get("status_code"),
        "sub_status_code": n.get("sub_status_code"),
        "exceptions": n.get("exceptions") or [],
        "exceptions_raw": n.get("exceptions_raw"),
        "exception_details": n.get("exception_details") or [],
        "exception_summary": n.get("exception_summary") or "",
        "message": health.get("reason") if not reachable else (snap.get("last_error") or "Registered with printer"),
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
        "layer_source": print_metrics.get("layer_source"),
        "layer_total_missing": print_metrics.get("layer_total_missing"),
        "hotend_current": nozzle.get("actual"),
        "hotend_target": nozzle.get("target"),
        "bed_current": bed.get("actual"),
        "bed_target": bed.get("target"),
        "chamber_current": chamber.get("actual"),
        "chamber_target": chamber.get("target"),
        "light_on": _extract_light_on(n, snap.get("raw_status") or {}),
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
    status["print_phase"] = _print_phase_from_status(status, snap) if reachable else {"is_preparing": False, "kind": status.get("connection_state") or "offline", "label": status.get("status_text") or "Offline", "status_code": n.get("status_code"), "sub_status_code": n.get("sub_status_code")}
    status["active_print"] = _status_looks_active_print(status, snap) if reachable else False
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
            "state": "standing_by" if status.get("reachable") else "printer_offline",
            "level": "low" if status.get("reachable") else "watch",
            "risk": 0,
            "summary": "Standing By" if status.get("reachable") else (status.get("status_text") or "Offline"),
            "reasons": ["Kiosk is using the fast cached AI path; background AI will update this badge when available." if status.get("reachable") else (status.get("connection_reason") or "Printer telemetry is disconnected.")],
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
    status = _status_from_snapshot(pid, printer, snap)
    return await _enrich_status_with_file_layer_total(pid, status)


@app.get("/api/status/{printer_id}")
async def api_status_printer(printer_id: str):
    cfg = load_config()
    printer = cfg.get("printers", {}).get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
    snap = runtime.snapshot(printer_id)
    status = _status_from_snapshot(printer_id, printer, snap)
    return await _enrich_status_with_file_layer_total(printer_id, status)


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


@app.post("/api/ai/enabled")
async def api_ai_set_enabled(req: AIEnabledRequest):
    cfg = load_config()
    cfg.setdefault("portal_ai", {})["enabled"] = bool(req.enabled)
    saved = save_config(cfg)
    if not req.enabled:
        _AI_AUTO_PAUSE_PENDING.clear()
    log("info", f"Failure Detection {'enabled' if req.enabled else 'disabled'}", "settings")
    return {"ok": True, "enabled": bool(req.enabled), "config": saved.get("portal_ai", {})}


@app.post("/api/printers/{printer_id}/ai/auto-pause/cancel")
async def api_ai_auto_pause_cancel(printer_id: str, req: AutoPauseCancelRequest | None = None):
    pending = _AI_AUTO_PAUSE_PENDING.pop(printer_id, None)
    token = (req.token if req else None) or (pending or {}).get("token")
    cfg = load_config()
    ai_cfg = cfg.get("portal_ai", {}) or {}
    cooldown_minutes = max(1.0, min(240.0, _coerce_float(ai_cfg.get("auto_pause_cooldown_minutes"), 10.0)))
    if token:
        _AI_AUTO_PAUSE_CANCELLED[str(token)] = time.time() + cooldown_minutes * 60.0
    log("info", f"Failure Detection auto-pause cancelled{': ' + str(req.reason) if req and req.reason else ''}", "portal_ai", printer=printer_id)
    return {"ok": True, "cancelled": bool(pending), "token": token, "cooldown_minutes": cooldown_minutes}


@app.post("/api/printers/{printer_id}/ai/auto-pause/pause-now")
async def api_ai_auto_pause_now(printer_id: str, req: AutoPauseNowRequest | None = None):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    pending = _AI_AUTO_PAUSE_PENDING.get(printer_id)
    if not pending:
        raise HTTPException(409, "No active Failure Detection auto-pause warning is pending.")
    requested_token = str((req.token if req else None) or "").strip()
    if requested_token and requested_token != str(pending.get("token") or ""):
        raise HTTPException(409, "The auto-pause warning token no longer matches the active warning.")

    recheck = await asyncio.to_thread(_auto_pause_fresh_recheck, printer_id, cfg, pending)
    gate = recheck.get("gate") if isinstance(recheck.get("gate"), dict) else {}
    if not recheck.get("ok") or not gate.get("pause_allowed"):
        reason = str(gate.get("reason") or recheck.get("error") or "Fresh recheck did not permit pausing.")
        raise HTTPException(409, f"Pause blocked: {reason}")

    result = await asyncio.to_thread(_send_command, printer_id, PAUSE_PRINT, {}, True, 60.0, True)
    _AI_AUTO_PAUSE_PENDING.pop(printer_id, None)
    _AI_AUTO_PAUSE_LAST_SENT[printer_id] = time.time()
    log("warning", f"Failure Detection pause-now command sent after recheck: {gate.get('failure_family') or 'unknown'}", "portal_ai", printer=printer_id)
    return {"ok": True, "message": "Pause command sent after a fresh safety recheck.", "result": result.get("result"), "permission": gate}


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




def _feedback_frame_url(relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    return f"/api/ai/feedback/frame?path={quote(str(relative_path), safe='')}"


def _feedback_frame_info_from_path(frame_path: str | None, source: str = "annotation_frame") -> dict[str, Any] | None:
    path = _feedback_frame_path(frame_path)
    if not path:
        return None
    try:
        rel = str(path.relative_to(DATA_DIR)) if DATA_DIR in path.parents else str(path)
    except Exception:
        rel = str(path)
    try:
        size = path.stat().st_size
    except Exception:
        size = 0
    return {
        "captured": True,
        "fresh": False,
        "source": source,
        "path": str(path),
        "relative_path": rel,
        "url": _feedback_frame_url(rel),
        "bytes": size,
    }


def _clamp_unit_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if out != out:  # NaN
            return default
    except Exception:
        return default
    return max(0.0, min(1.0, out))


def _normalize_roi_annotation(annotation: dict[str, Any] | None, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not isinstance(annotation, dict):
        return None
    box = annotation.get("box") if isinstance(annotation.get("box"), dict) else annotation
    if not isinstance(box, dict):
        return None
    x = _clamp_unit_float(box.get("x"), 0.0)
    y = _clamp_unit_float(box.get("y"), 0.0)
    w = _clamp_unit_float(box.get("w"), 0.0)
    h = _clamp_unit_float(box.get("h"), 0.0)
    if x + w > 1.0:
        w = max(0.0, 1.0 - x)
    if y + h > 1.0:
        h = max(0.0, 1.0 - y)
    if w < 0.015 or h < 0.015:
        return None
    failure_type = str(annotation.get("failure_type") or annotation.get("reason_key") or (context or {}).get("failure_type") or "unknown").strip().lower()
    failure_type = re.sub(r"[^a-z0-9_\-]+", "_", failure_type)[:64] or "unknown"
    reason = str(annotation.get("reason") or annotation.get("reason_text") or (context or {}).get("reason") or "").strip()[:240]
    source = str(annotation.get("source") or "dashboard_roi_modal").strip()[:80] or "dashboard_roi_modal"
    return {
        "schema": "cc2-ai-roi-annotation-v1",
        "type": "box",
        "x": round(x, 6),
        "y": round(y, 6),
        "w": round(w, 6),
        "h": round(h, 6),
        "failure_type": failure_type,
        "reason": reason,
        "source": source,
        "created_at_epoch": time.time(),
        "display": annotation.get("display") if isinstance(annotation.get("display"), dict) else {},
    }


def _safe_crop_name(value: Any, fallback: str = "roi") -> str:
    text = str(value or fallback).strip().lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_\-.]+", "_", text).strip("._-")
    return text[:72] or fallback


def _attach_roi_crops(frame_info: dict[str, Any] | None, annotation: dict[str, Any] | None) -> dict[str, Any] | None:
    """Save ROI crops next to the feedback frame and return annotation metadata.

    Uses normalized coordinates so the same box survives phone browsers, rotated
    displays, and camera resolution changes.
    """
    ann = _normalize_roi_annotation(annotation)
    if not ann:
        return None
    frame_path = _feedback_frame_path((frame_info or {}).get("relative_path") or (frame_info or {}).get("path"))
    if not frame_path:
        ann["crop_error"] = "feedback_frame_not_found"
        return ann
    if Image is None:
        ann["crop_error"] = "pillow_unavailable"
        return ann
    try:
        with Image.open(frame_path) as img:
            img = img.convert("RGB")
            width, height = img.size
            x1 = int(round(ann["x"] * width))
            y1 = int(round(ann["y"] * height))
            x2 = int(round((ann["x"] + ann["w"]) * width))
            y2 = int(round((ann["y"] + ann["h"]) * height))
            x1 = max(0, min(width - 1, x1))
            y1 = max(0, min(height - 1, y1))
            x2 = max(x1 + 1, min(width, x2))
            y2 = max(y1 + 1, min(height, y2))
            pad_x = max(8, int(round((x2 - x1) * 0.25)))
            pad_y = max(8, int(round((y2 - y1) * 0.25)))
            px1 = max(0, x1 - pad_x)
            py1 = max(0, y1 - pad_y)
            px2 = min(width, x2 + pad_x)
            py2 = min(height, y2 + pad_y)
            root = frame_path.parent
            crop_stem = f"{frame_path.stem}_{_safe_crop_name(ann.get('failure_type'))}_{uuid.uuid4().hex[:6]}"
            crop_path = root / f"{crop_stem}_roi.jpg"
            padded_path = root / f"{crop_stem}_roi_context.jpg"
            img.crop((x1, y1, x2, y2)).save(crop_path, format="JPEG", quality=90)
            img.crop((px1, py1, px2, py2)).save(padded_path, format="JPEG", quality=90)
            ann["image_size"] = {"width": width, "height": height}
            ann["pixel_box"] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            ann["pixel_box_padded"] = {"x1": px1, "y1": py1, "x2": px2, "y2": py2}
            crop_rel = str(crop_path.relative_to(DATA_DIR)) if DATA_DIR in crop_path.parents else str(crop_path)
            padded_rel = str(padded_path.relative_to(DATA_DIR)) if DATA_DIR in padded_path.parents else str(padded_path)
            ann["crops"] = {
                "roi_path": str(crop_path),
                "roi_relative_path": crop_rel,
                "roi_url": _feedback_frame_url(crop_rel),
                "roi_bytes": crop_path.stat().st_size,
                "context_path": str(padded_path),
                "context_relative_path": padded_rel,
                "context_url": _feedback_frame_url(padded_rel),
                "context_bytes": padded_path.stat().st_size,
            }
    except Exception as exc:
        ann["crop_error"] = str(exc)
    return ann


def _feedback_annotation_source_path(annotation: dict[str, Any] | None, context: dict[str, Any] | None = None) -> str | None:
    ann = annotation if isinstance(annotation, dict) else {}
    ctx = context if isinstance(context, dict) else {}
    return (
        ann.get("source_frame_path")
        or ann.get("frame_path")
        or ctx.get("feedback_frame_path")
        or ctx.get("source_frame_path")
    )

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


@app.get("/api/ai/feedback/frame")
async def api_ai_feedback_frame(path: str):
    frame_path = _feedback_frame_path(path)
    if not frame_path:
        raise HTTPException(status_code=404, detail="Feedback frame not found")
    media = "image/png" if frame_path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(str(frame_path), media_type=media, headers={"Cache-Control": "private, max-age=60"})


@app.post("/api/printers/{printer_id}/ai/feedback/frame")
async def api_printer_ai_feedback_frame(printer_id: str, body: AIFeedbackFrameRequest | None = None):
    cfg = load_config()
    printer = cfg.get("printers", {}).get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
    label = str((body.label if body else "missed_failure") or "missed_failure").strip() or "missed_failure"
    frame_info = _capture_feedback_frame(printer_id, printer, cfg, label)
    if not frame_info or not frame_info.get("captured"):
        detail = (frame_info or {}).get("error") or "Could not capture camera frame"
        raise HTTPException(status_code=503, detail=detail)
    frame_info.pop("heuristics", None)
    frame_info["url"] = _feedback_frame_url(frame_info.get("relative_path"))
    return {"ok": True, "printer_id": printer_id, "frame": frame_info}

def _json_maybe(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return fallback
        try:
            return json.loads(raw)
        except Exception:
            return fallback
    return fallback


def _feedback_frame_path(frame_path: str | None) -> Path | None:
    raw = str(frame_path or "").strip()
    if not raw:
        return None
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = DATA_DIR / candidate
        candidate = candidate.resolve()
        root = (DATA_DIR / "ai_feedback_frames").resolve()
        if candidate != root and root not in candidate.parents:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate
    except Exception:
        return None


def _public_learning_sample(sample: dict[str, Any]) -> dict[str, Any]:
    raw_obj = _json_maybe(sample.get("raw_json"), {})
    snapshot = raw_obj.get("snapshot") if isinstance(raw_obj, dict) and isinstance(raw_obj.get("snapshot"), dict) else {}
    reason = str(sample.get("feedback_note") or raw_obj.get("reason") or snapshot.get("reason") or raw_obj.get("note") or snapshot.get("note") or "").strip()
    reason_key = str(raw_obj.get("reason_key") or snapshot.get("reason_key") or "").strip() if isinstance(raw_obj, dict) else ""
    flags = _json_maybe(sample.get("triggered_flags"), [])
    if isinstance(flags, str):
        flags = [flags]
    if not isinstance(flags, list):
        flags = []
    sample_id = int(sample.get("id") or 0)
    frame_path = str(sample.get("frame_path") or "").strip()
    has_frame = bool(_feedback_frame_path(frame_path))
    return {
        "id": sample_id,
        "created_at": sample.get("created_at"),
        "printer_id": sample.get("printer_id"),
        "feedback_label": sample.get("feedback_label"),
        "feedback_note": reason,
        "reason": reason,
        "reason_key": reason_key,
        "outcome": sample.get("outcome"),
        "ai_was_warning": bool(sample.get("ai_was_warning")),
        "user_says_failure": bool(sample.get("user_says_failure")),
        "file_name": sample.get("file_name"),
        "print_stage": sample.get("print_stage"),
        "progress_percent": sample.get("progress_percent"),
        "risk_score": sample.get("risk_score"),
        "severity": sample.get("severity"),
        "confidence": sample.get("confidence"),
        "vision_state": sample.get("vision_state"),
        "dark_luma": sample.get("dark_luma"),
        "contrast": sample.get("contrast"),
        "edge_density": sample.get("edge_density"),
        "edge_delta": sample.get("edge_delta"),
        "triggered_flags": [str(x) for x in flags if str(x).strip()],
        "suppression_match": bool(sample.get("suppression_match")),
        "model_name": sample.get("model_name"),
        "prompt_version": sample.get("prompt_version"),
        "frame_path": frame_path,
        "has_frame": has_frame,
        "frame_url": f"/api/ai/learning/samples/{sample_id}/frame" if sample_id and has_frame else None,
        "has_annotation": isinstance(snapshot.get("annotation"), dict) and bool(snapshot.get("annotation")),
        "annotation": snapshot.get("annotation") if isinstance(snapshot.get("annotation"), dict) else None,
        "roi_frame_url": f"/api/ai/learning/samples/{sample_id}/roi-frame?variant=context" if sample_id and isinstance(snapshot.get("annotation"), dict) and isinstance((snapshot.get("annotation") or {}).get("crops"), dict) else None,
        "has_raw_json": bool(sample.get("raw_json")),
    }


def _clean_sample_filter(value: str | None, allowed: set[str] | None = None) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"all", "any", "*"}:
        return None
    return text if allowed is None or text in allowed else None


def _export_safe_name(value: Any, fallback: str = "sample") -> str:
    text = str(value or fallback).strip().replace("\\", "_").replace("/", "_")
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)
    return text[:90] or fallback


def _training_export_zip(rows: list[dict[str, Any]], include_frames: bool = True) -> bytes:
    buf = io.BytesIO()
    public_rows = [_public_learning_sample(r) for r in rows]
    manifest = {
        "schema": "cc2-ai-training-export-v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sample_count": len(public_rows),
        "frames_included": bool(include_frames),
        "note": "SQLite training review export. JSONL audit log remains stored separately on the device.",
    }
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, default=str))
        zf.writestr("samples_public.json", json.dumps(public_rows, indent=2, ensure_ascii=False, default=str))
        jsonl = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + ("\n" if rows else "")
        zf.writestr("samples_raw.jsonl", jsonl)
        if include_frames:
            seen: set[str] = set()
            for row in rows:
                sample_id = int(row.get("id") or 0)
                path = _feedback_frame_path(row.get("frame_path"))
                if not path:
                    continue
                resolved = str(path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                suffix = path.suffix.lower() or ".jpg"
                zf.write(path, f"frames/{sample_id}_{_export_safe_name(path.stem)}{suffix}")
                raw_obj = _json_maybe(row.get("raw_json"), {})
                snapshot = raw_obj.get("snapshot") if isinstance(raw_obj, dict) and isinstance(raw_obj.get("snapshot"), dict) else {}
                ann = snapshot.get("annotation") if isinstance(snapshot.get("annotation"), dict) else {}
                crops = ann.get("crops") if isinstance(ann.get("crops"), dict) else {}
                for crop_key in ("roi_relative_path", "context_relative_path"):
                    crop_path = _feedback_frame_path(crops.get(crop_key))
                    if not crop_path:
                        continue
                    resolved_crop = str(crop_path.resolve())
                    if resolved_crop in seen:
                        continue
                    seen.add(resolved_crop)
                    crop_suffix = crop_path.suffix.lower() or ".jpg"
                    zf.write(crop_path, f"roi_frames/{sample_id}_{crop_key}_{_export_safe_name(crop_path.stem)}{crop_suffix}")
    return buf.getvalue()



def _sqlite_table_rows(table: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return rows from a trusted AI-learning SQLite table."""
    if table not in {"feedback_samples", "learning_profiles", "learning_events", "schema_meta"}:
        return []
    path = Path(db_path or ai_learning_db.DB_PATH)
    if not path.exists():
        return []
    try:
        with sqlite3.connect(str(path), timeout=3.0) as conn:
            conn.row_factory = sqlite3.Row
            if table == "feedback_samples":
                rows = conn.execute("SELECT * FROM feedback_samples ORDER BY created_at DESC, id DESC").fetchall()
            elif table == "learning_events":
                rows = conn.execute("SELECT * FROM learning_events ORDER BY id ASC").fetchall()
            elif table == "learning_profiles":
                rows = conn.execute("SELECT * FROM learning_profiles ORDER BY updated_at DESC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM schema_meta ORDER BY key ASC").fetchall()
            return [ai_learning_db.row_to_dict(r) or {} for r in rows]
    except Exception as exc:
        log("warning", f"AI learning backup could not read table {table}: {exc}", "portal_ai")
        return []


def _iter_feedback_frame_paths_from_rows(rows: list[dict[str, Any]]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for row in rows or []:
        candidates: list[Any] = [row.get("frame_path")]
        raw_obj = _json_maybe(row.get("raw_json"), {})
        snapshot = raw_obj.get("snapshot") if isinstance(raw_obj, dict) and isinstance(raw_obj.get("snapshot"), dict) else {}
        ann = snapshot.get("annotation") if isinstance(snapshot.get("annotation"), dict) else {}
        crops = ann.get("crops") if isinstance(ann.get("crops"), dict) else {}
        candidates.extend([crops.get("roi_relative_path"), crops.get("context_relative_path")])
        for candidate in candidates:
            path = _feedback_frame_path(candidate)
            if not path:
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(path)
    return out


def _write_sqlite_backup_to_zip(zf: zipfile.ZipFile) -> dict[str, Any]:
    """Write a consistent SQLite backup into the ZIP using sqlite3 backup()."""
    ai_learning_db.ensure_database()
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="cc2_ai_learning_", suffix=".sqlite3", delete=False) as tmp:
            tmp_name = tmp.name
        with sqlite3.connect(str(ai_learning_db.DB_PATH), timeout=5.0) as src, sqlite3.connect(tmp_name) as dst:
            src.backup(dst)
        zf.write(tmp_name, "data/ai_learning.sqlite3")
        size = Path(tmp_name).stat().st_size
        return {"included": True, "bytes": size}
    except Exception as exc:
        log("warning", f"AI learning SQLite backup failed: {exc}", "portal_ai")
        return {"included": False, "error": str(exc)}
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass


def _training_backup_zip(include_frames: bool = True, include_sqlite: bool = True, include_jsonl: bool = True) -> tuple[bytes, dict[str, Any]]:
    """Create a restore-capable AI learning backup ZIP."""
    ai_learning_db.ensure_database()
    rows = _sqlite_table_rows("feedback_samples")
    profiles = _sqlite_table_rows("learning_profiles")
    events = _sqlite_table_rows("learning_events")
    schema_rows = _sqlite_table_rows("schema_meta")
    health = ai_learning_db.health()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest: dict[str, Any] = {
        "schema": "cc2-ai-learning-backup-v1",
        "created_at": stamp,
        "app": "cc2-dash",
        "app_version": __version__,
        "db_schema_version": health.get("schema_version"),
        "sample_count": len(rows),
        "profile_count": len(profiles),
        "event_count": len(events),
        "frames_included": bool(include_frames),
        "sqlite_included": bool(include_sqlite),
        "jsonl_included": bool(include_jsonl),
        "restore_modes": ["merge", "replace"],
        "note": "Full AI learning backup. Import in replace mode overwrites the current SQLite learner, JSONL audit log, and feedback frame library after creating a local pre-import backup.",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if include_sqlite:
            manifest["sqlite_backup"] = _write_sqlite_backup_to_zip(zf)
        zf.writestr("data/samples_raw.jsonl", "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + ("\n" if rows else ""))
        zf.writestr("data/samples_public.json", json.dumps([_public_learning_sample(r) for r in rows], indent=2, ensure_ascii=False, default=str))
        zf.writestr("data/learning_profiles.json", json.dumps(profiles, indent=2, ensure_ascii=False, default=str))
        zf.writestr("data/learning_events.json", json.dumps(events, indent=2, ensure_ascii=False, default=str))
        zf.writestr("data/schema_meta.json", json.dumps(schema_rows, indent=2, ensure_ascii=False, default=str))
        if include_jsonl:
            audit_path = DATA_DIR / "ai_feedback.jsonl"
            if audit_path.exists() and audit_path.is_file():
                zf.write(audit_path, "data/ai_feedback.jsonl")
                manifest["jsonl_bytes"] = audit_path.stat().st_size
            else:
                zf.writestr("data/ai_feedback.jsonl", "")
                manifest["jsonl_bytes"] = 0
        frame_count = 0
        if include_frames:
            for path in _iter_feedback_frame_paths_from_rows(rows):
                try:
                    rel = path.relative_to(DATA_DIR / "ai_feedback_frames")
                    arcname = "data/ai_feedback_frames/" + str(rel).replace("\\", "/")
                except Exception:
                    arcname = "data/ai_feedback_frames/imported/" + _export_safe_name(path.name, "frame.jpg")
                zf.write(path, arcname)
                frame_count += 1
        manifest["frame_file_count"] = frame_count
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, default=str))
    return buf.getvalue(), manifest


def _zip_read_json(zf: zipfile.ZipFile, name: str, fallback: Any = None) -> Any:
    try:
        with zf.open(name) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        return fallback


def _read_zip_text_lines(zf: zipfile.ZipFile, names: list[str]) -> list[str]:
    for name in names:
        try:
            with zf.open(name) as fh:
                text = fh.read().decode("utf-8", errors="replace")
                return [line for line in text.splitlines() if line.strip()]
        except KeyError:
            continue
        except Exception:
            return []
    return []


def _preview_learning_backup_bytes(blob: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
            names = zf.namelist()
            manifest = _zip_read_json(zf, "manifest.json", {})
            schema = str((manifest or {}).get("schema") or "unknown")
            sqlite_names = [n for n in names if n in {"data/ai_learning.sqlite3", "ai_learning.sqlite3", "learning/ai_learning.sqlite3"}]
            jsonl_lines = _read_zip_text_lines(zf, ["data/samples_raw.jsonl", "samples_raw.jsonl", "learning/samples_raw.jsonl"])
            audit_present = any(n in names for n in ("data/ai_feedback.jsonl", "ai_feedback.jsonl"))
            frame_names = [n for n in names if n.startswith("data/ai_feedback_frames/") or n.startswith("frames/") or n.startswith("roi_frames/")]
            db_counts: dict[str, Any] = {}
            if sqlite_names:
                tmp_name: str | None = None
                try:
                    with tempfile.NamedTemporaryFile(prefix="cc2_ai_import_preview_", suffix=".sqlite3", delete=False) as tmp:
                        tmp.write(zf.read(sqlite_names[0]))
                        tmp_name = tmp.name
                    db_counts = ai_learning_db.health(Path(tmp_name))
                except Exception as exc:
                    db_counts = {"ok": False, "error": str(exc)}
                finally:
                    if tmp_name:
                        try:
                            Path(tmp_name).unlink(missing_ok=True)
                        except Exception:
                            pass
            sample_count = int((manifest or {}).get("sample_count") or db_counts.get("feedback_samples") or len(jsonl_lines) or 0)
            profile_count = int((manifest or {}).get("profile_count") or db_counts.get("profiles") or 0)
            event_count = int((manifest or {}).get("event_count") or db_counts.get("events") or 0)
            warnings = [
                "Replace mode overwrites the current AI learning SQLite database, JSONL audit log, and feedback frame library.",
                "A local pre-import backup ZIP is created before replace mode changes anything.",
            ]
            if not sqlite_names:
                warnings.append("This ZIP does not include a SQLite database; import will rebuild samples from JSONL where possible.")
            if not frame_names:
                warnings.append("No frame files were found in this ZIP, so restored samples may not have thumbnails/ROI crops.")
            return {
                "ok": True,
                "schema": schema,
                "manifest": manifest or {},
                "zip_bytes": len(blob),
                "file_count": len(names),
                "has_sqlite": bool(sqlite_names),
                "has_jsonl_audit": audit_present,
                "sample_count": sample_count,
                "profile_count": profile_count,
                "event_count": event_count,
                "frame_file_count": len(frame_names),
                "warnings": warnings,
                "supports_replace": bool(sqlite_names or jsonl_lines),
                "supports_merge": bool(jsonl_lines),
            }
    except zipfile.BadZipFile:
        return {"ok": False, "error": "uploaded_file_is_not_a_zip"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _safe_backup_member_suffix(name: str, prefix: str) -> Path | None:
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix):].lstrip("/")
    if not suffix or suffix.endswith("/"):
        return None
    parts = Path(suffix).parts
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return Path(*parts)


def _restore_frame_members(zf: zipfile.ZipFile, replace_existing: bool = False) -> dict[str, Any]:
    frame_root = DATA_DIR / "ai_feedback_frames"
    frame_root.mkdir(parents=True, exist_ok=True)
    restored = 0
    skipped = 0
    errors: list[str] = []
    if replace_existing:
        try:
            shutil.rmtree(frame_root, ignore_errors=True)
            frame_root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            errors.append(f"could_not_clear_frame_library: {exc}")
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        suffix = _safe_backup_member_suffix(name, "data/ai_feedback_frames/")
        # Legacy dataset export had anonymized frame filenames. Keep them for human review,
        # but those files cannot always relink to SQLite rows because the original relative
        # path was intentionally not preserved in that older export format.
        legacy_prefix = None
        if suffix is None and name.startswith("frames/"):
            suffix = _safe_backup_member_suffix(name, "frames/")
            legacy_prefix = "legacy_frames"
        if suffix is None and name.startswith("roi_frames/"):
            suffix = _safe_backup_member_suffix(name, "roi_frames/")
            legacy_prefix = "legacy_roi_frames"
        if suffix is None:
            continue
        target = (frame_root / legacy_prefix / suffix if legacy_prefix else frame_root / suffix).resolve()
        root_resolved = frame_root.resolve()
        if target != root_resolved and root_resolved not in target.parents:
            skipped += 1
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            restored += 1
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {"restored": restored, "skipped": skipped, "errors": errors[:10]}


def _clear_learning_sqlite() -> None:
    ai_learning_db.ensure_database()
    with ai_learning_db.connect() as conn:
        conn.execute("DELETE FROM feedback_samples")
        conn.execute("DELETE FROM learning_profiles")
        conn.execute("DELETE FROM learning_events")
        ai_learning_db.log_event(
            "learning_import_replace_clear",
            printer_id=None,
            level="warning",
            message="AI learning database cleared for backup restore.",
            conn=conn,
            raw={"source": "backup_import"},
        )


def _replace_sqlite_from_zip(zf: zipfile.ZipFile) -> dict[str, Any]:
    sqlite_name = next((n for n in ("data/ai_learning.sqlite3", "learning/ai_learning.sqlite3", "ai_learning.sqlite3") if n in zf.namelist()), None)
    if not sqlite_name:
        return {"replaced": False, "reason": "sqlite_not_in_zip"}
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="cc2_ai_import_db_", suffix=".sqlite3", delete=False) as tmp:
            tmp.write(zf.read(sqlite_name))
            tmp_name = tmp.name
        imported_health = ai_learning_db.health(Path(tmp_name))
        if not imported_health.get("ok"):
            raise RuntimeError(imported_health.get("error") or "imported SQLite health check failed")
        db_path = ai_learning_db.DB_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            except Exception:
                pass
        shutil.copy2(tmp_name, db_path)
        ai_learning_db.ensure_database(db_path)
        return {"replaced": True, "source": sqlite_name, "health": ai_learning_db.health(db_path), "imported_health": imported_health}
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass


def _import_samples_from_zip_jsonl(zf: zipfile.ZipFile) -> dict[str, Any]:
    lines = _read_zip_text_lines(zf, ["data/samples_raw.jsonl", "samples_raw.jsonl", "learning/samples_raw.jsonl"])
    inserted = 0
    duplicates = 0
    errors = 0
    for line in lines:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                errors += 1
                continue
            res = ai_learning_db.insert_feedback_sample(row)
            if res.get("inserted"):
                inserted += 1
            elif res.get("duplicate"):
                duplicates += 1
        except Exception:
            errors += 1
    return {"rows": len(lines), "inserted": inserted, "duplicates": duplicates, "errors": errors}


def _restore_audit_jsonl(zf: zipfile.ZipFile, mode: str = "merge") -> dict[str, Any]:
    name = next((n for n in ("data/ai_feedback.jsonl", "ai_feedback.jsonl") if n in zf.namelist()), None)
    if not name:
        return {"present": False, "written": False}
    target = DATA_DIR / "ai_feedback.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = zf.read(name)
    if mode == "replace":
        target.write_bytes(data)
        return {"present": True, "written": True, "mode": "replace", "bytes": len(data)}
    # Merge mode appends the imported audit log with a separator comment-like JSON event.
    with target.open("ab") as fh:
        if target.exists() and target.stat().st_size > 0 and not target.read_bytes().endswith(b"\n"):
            fh.write(b"\n")
        marker = {"schema": "cc2-ai-feedback-import-marker-v1", "kind": "backup_import_merge", "timestamp": time.time(), "source": name}
        fh.write(json.dumps(marker, ensure_ascii=False).encode("utf-8") + b"\n")
        fh.write(data)
        if data and not data.endswith(b"\n"):
            fh.write(b"\n")
    return {"present": True, "written": True, "mode": "merge", "bytes": len(data)}


def _restore_learning_backup_bytes(blob: bytes, mode: str = "merge", rebuild_profiles: bool = True) -> dict[str, Any]:
    mode = str(mode or "merge").strip().lower()
    if mode not in {"merge", "replace"}:
        mode = "merge"
    preview = _preview_learning_backup_bytes(blob)
    if not preview.get("ok"):
        return {"ok": False, "error": preview.get("error") or "invalid_backup_zip", "preview": preview}
    preimport_backup = None
    if mode == "replace":
        try:
            backup_bytes, backup_manifest = _training_backup_zip(include_frames=True, include_sqlite=True, include_jsonl=True)
            backup_root = DATA_DIR / "ai_import_backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            name = f"cc2-dash-ai-preimport-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.zip"
            backup_path = backup_root / name
            backup_path.write_bytes(backup_bytes)
            preimport_backup = {"path": str(backup_path), "bytes": backup_path.stat().st_size, "manifest": backup_manifest}
        except Exception as exc:
            return {"ok": False, "error": f"preimport_backup_failed: {exc}", "preview": preview}
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        frame_result = _restore_frame_members(zf, replace_existing=(mode == "replace"))
        audit_result = _restore_audit_jsonl(zf, mode=mode)
        sqlite_result: dict[str, Any] = {"replaced": False}
        samples_result: dict[str, Any] = {"rows": 0, "inserted": 0, "duplicates": 0, "errors": 0}
        if mode == "replace":
            sqlite_result = _replace_sqlite_from_zip(zf)
            if not sqlite_result.get("replaced"):
                _clear_learning_sqlite()
                samples_result = _import_samples_from_zip_jsonl(zf)
        else:
            samples_result = _import_samples_from_zip_jsonl(zf)
    cfg = load_config()
    rebuild_results: list[dict[str, Any]] = []
    if rebuild_profiles:
        for pid in ai_learning.known_printer_ids(cfg):
            try:
                rebuild_results.append(ai_learning.rebuild_profile(pid, cfg))
            except Exception as exc:
                rebuild_results.append({"printer_id": pid, "ok": False, "error": str(exc)})
    status = ai_learning.global_status(cfg)
    try:
        ai_learning_db.log_event(
            "learning_backup_imported",
            printer_id=None,
            level="warning" if mode == "replace" else "info",
            message=f"AI learning backup imported in {mode} mode.",
            raw={"mode": mode, "preview": preview, "frames": frame_result, "sqlite": sqlite_result, "samples": samples_result, "preimport_backup": preimport_backup},
        )
    except Exception:
        pass
    return {
        "ok": True,
        "mode": mode,
        "preview": preview,
        "frames": frame_result,
        "audit_log": audit_result,
        "sqlite": sqlite_result,
        "samples": samples_result,
        "rebuild_profiles": rebuild_results,
        "preimport_backup": preimport_backup,
        "status": status,
    }


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


@app.post("/api/ai/learning/import-jsonl")
async def api_ai_learning_import_jsonl(body: LearningImportRequest | None = None):
    cfg = load_config()
    rebuild_profiles = bool(body.rebuild_profiles) if body else True
    limit = body.limit if body else None
    result = await asyncio.to_thread(ai_learning.import_jsonl_feedback, None, cfg, rebuild_profiles, limit)
    status = await asyncio.to_thread(ai_learning.global_status, cfg)
    return {"ok": bool(result.get("ok", True)), "import": result, "status": status}


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


@app.get("/api/ai/learning/samples")
async def api_ai_learning_samples(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    printer_id: str | None = None,
    outcome: str | None = None,
    label: str | None = None,
):
    cfg = load_config()
    known = set(ai_learning.known_printer_ids(cfg))
    pid = _clean_sample_filter(printer_id)
    if pid and pid not in known:
        raise HTTPException(status_code=404, detail="Printer not configured")
    outcome_filter = _clean_sample_filter(outcome, {"true_positive", "false_positive", "false_negative", "true_negative"})
    label_filter = _clean_sample_filter(label, {"looks_good", "looks_bad", "false_alarm"})
    rows, total = await asyncio.gather(
        asyncio.to_thread(ai_learning_db.fetch_recent_samples, pid, limit, offset, outcome_filter, label_filter),
        asyncio.to_thread(ai_learning_db.count_recent_samples, pid, outcome_filter, label_filter),
    )
    samples = [_public_learning_sample(row) for row in rows]
    return {
        "ok": True,
        "count": len(samples),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(samples) < total,
        "filters": {"printer_id": pid, "outcome": outcome_filter, "label": label_filter},
        "printer_ids": sorted(known),
        "samples": samples,
    }


@app.get("/api/ai/learning/samples/{sample_id}/frame")
async def api_ai_learning_sample_frame(sample_id: int):
    sample = await asyncio.to_thread(ai_learning_db.get_sample, sample_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Feedback sample not found")
    path = _feedback_frame_path(sample.get("frame_path"))
    if not path:
        raise HTTPException(status_code=404, detail="Feedback frame not found")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(str(path), media_type=media, headers={"Cache-Control": "private, max-age=60"})


@app.get("/api/ai/learning/samples/{sample_id}/roi-frame")
async def api_ai_learning_sample_roi_frame(sample_id: int, variant: str = Query("context")):
    sample = await asyncio.to_thread(ai_learning_db.get_sample, sample_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Feedback sample not found")
    raw_obj = _json_maybe(sample.get("raw_json"), {})
    snapshot = raw_obj.get("snapshot") if isinstance(raw_obj, dict) and isinstance(raw_obj.get("snapshot"), dict) else {}
    ann = snapshot.get("annotation") if isinstance(snapshot.get("annotation"), dict) else {}
    crops = ann.get("crops") if isinstance(ann.get("crops"), dict) else {}
    key = "roi_relative_path" if str(variant or "").lower() == "roi" else "context_relative_path"
    path = _feedback_frame_path(crops.get(key) or crops.get("context_relative_path") or crops.get("roi_relative_path"))
    if not path:
        raise HTTPException(status_code=404, detail="ROI crop not found")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(str(path), media_type=media, headers={"Cache-Control": "private, max-age=60"})


@app.post("/api/ai/learning/samples/{sample_id}/review")
async def api_ai_learning_sample_review(sample_id: int, body: LearningSampleReviewRequest):
    result = await asyncio.to_thread(
        ai_learning_db.update_sample_review,
        sample_id,
        body.feedback_label,
        body.outcome,
        body.feedback_note,
        body.reason_key,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error") or "Feedback sample not found")
    if body.rebuild_profile:
        sample = await asyncio.to_thread(ai_learning_db.get_sample, sample_id)
        printer_id = str((sample or {}).get("printer_id") or result.get("printer_id") or "")
        if printer_id:
            try:
                await asyncio.to_thread(ai_learning.rebuild_profile, printer_id, load_config())
            except Exception as exc:
                log("warning", f"AI learning profile rebuild after review failed: {exc}", "portal_ai", printer=printer_id)
    sample = await asyncio.to_thread(ai_learning_db.get_sample, sample_id)
    return {"ok": True, "result": result, "sample": _public_learning_sample(sample or {})}


@app.delete("/api/ai/learning/samples/{sample_id}")
async def api_ai_learning_sample_delete(sample_id: int, rebuild_profile: bool = Query(True)):
    sample = await asyncio.to_thread(ai_learning_db.get_sample, sample_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Feedback sample not found")
    printer_id = str(sample.get("printer_id") or "")
    result = await asyncio.to_thread(ai_learning_db.delete_sample, sample_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error") or "Feedback sample not found")
    if rebuild_profile and printer_id:
        try:
            await asyncio.to_thread(ai_learning.rebuild_profile, printer_id, load_config())
        except Exception as exc:
            log("warning", f"AI learning profile rebuild after sample delete failed: {exc}", "portal_ai", printer=printer_id)
    return {"ok": True, "result": result}


@app.get("/api/ai/learning/export")
async def api_ai_learning_export(
    limit: int = Query(5000, ge=1, le=5000),
    printer_id: str | None = None,
    outcome: str | None = None,
    label: str | None = None,
    include_frames: bool = Query(True),
):
    cfg = load_config()
    known = set(ai_learning.known_printer_ids(cfg))
    pid = _clean_sample_filter(printer_id)
    if pid and pid not in known:
        raise HTTPException(status_code=404, detail="Printer not configured")
    outcome_filter = _clean_sample_filter(outcome, {"true_positive", "false_positive", "false_negative", "true_negative"})
    label_filter = _clean_sample_filter(label, {"looks_good", "looks_bad", "false_alarm"})
    rows = await asyncio.to_thread(ai_learning_db.fetch_recent_samples, pid, limit, 0, outcome_filter, label_filter)
    payload = await asyncio.to_thread(_training_export_zip, rows, include_frames)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    filename = f"cc2-dash-ai-training-{stamp}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/ai/learning/backup/export")
async def api_ai_learning_backup_export(
    include_frames: bool = Query(True),
    include_sqlite: bool = Query(True),
    include_jsonl: bool = Query(True),
):
    payload, manifest = await asyncio.to_thread(_training_backup_zip, include_frames, include_sqlite, include_jsonl)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    filename = f"cc2-dash-ai-learning-backup-{stamp}.zip"
    log("info", f"AI learning backup exported: {manifest.get('sample_count')} samples, frames={manifest.get('frame_file_count')}", "portal_ai")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/ai/learning/backup/import")
async def api_ai_learning_backup_import(
    file: UploadFile = File(...),
    mode: str = Form("merge"),
    preview_only: bool = Form(True),
    confirm_overwrite: bool = Form(False),
    rebuild_profiles: bool = Form(True),
):
    filename = str(file.filename or "learning-backup.zip")
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload a cc2-dash AI learning backup ZIP.")
    blob = await file.read()
    max_bytes = 750 * 1024 * 1024
    if len(blob) > max_bytes:
        raise HTTPException(status_code=413, detail="Backup ZIP is too large for web import.")
    preview = await asyncio.to_thread(_preview_learning_backup_bytes, blob)
    if not preview.get("ok"):
        raise HTTPException(status_code=400, detail=preview.get("error") or "Invalid backup ZIP")
    mode = str(mode or "merge").strip().lower()
    if mode not in {"merge", "replace"}:
        mode = "merge"
    if preview_only:
        return {"ok": True, "preview_only": True, "mode": mode, "backup": preview}
    if mode == "replace" and not confirm_overwrite:
        raise HTTPException(status_code=400, detail="Replace mode requires confirm_overwrite=true after previewing the backup.")
    result = await asyncio.to_thread(_restore_learning_backup_bytes, blob, mode, rebuild_profiles)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Import failed")
    log("warning" if mode == "replace" else "info", f"AI learning backup imported in {mode} mode: {preview.get('sample_count')} samples", "portal_ai")
    return result


@app.get("/api/printers/{printer_id}/ai/learning/samples")
async def api_printer_ai_learning_samples(
    printer_id: str,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    outcome: str | None = None,
    label: str | None = None,
):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}) and printer_id not in ai_learning.known_printer_ids(cfg):
        raise HTTPException(status_code=404, detail="Printer not configured")
    outcome_filter = _clean_sample_filter(outcome, {"true_positive", "false_positive", "false_negative", "true_negative"})
    label_filter = _clean_sample_filter(label, {"looks_good", "looks_bad", "false_alarm"})
    rows, total = await asyncio.gather(
        asyncio.to_thread(ai_learning_db.fetch_recent_samples, printer_id, limit, offset, outcome_filter, label_filter),
        asyncio.to_thread(ai_learning_db.count_recent_samples, printer_id, outcome_filter, label_filter),
    )
    samples = [_public_learning_sample(row) for row in rows]
    return {
        "ok": True,
        "printer_id": printer_id,
        "count": len(samples),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(samples) < total,
        "filters": {"printer_id": printer_id, "outcome": outcome_filter, "label": label_filter},
        "samples": samples,
    }


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
    source_frame_path = _feedback_annotation_source_path(body.annotation, body.context)
    frame_info = _feedback_frame_info_from_path(source_frame_path, source="roi_annotation_existing_frame") if source_frame_path else None
    if body.annotation and source_frame_path and not frame_info:
        raise HTTPException(status_code=400, detail="ROI annotation source frame was not found")
    if not frame_info:
        frame_info = _capture_feedback_frame(printer_id, printer, cfg, body.label)
    fresh_heuristics = (frame_info or {}).pop("heuristics", None) if isinstance(frame_info, dict) else None
    if isinstance(frame_info, dict) and frame_info.get("relative_path") and not frame_info.get("url"):
        frame_info["url"] = _feedback_frame_url(frame_info.get("relative_path"))
    annotation_info = _attach_roi_crops(frame_info, body.annotation) if body.annotation else None
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
        "annotation": annotation_info,
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
            "note": "Saved as labeled review data. False-positive feedback may suppress similar low/severity warnings for this active print only. ROI annotations are stored as review/crop evidence and do not change pause behavior yet. Heuristic thresholds are not overwritten.",
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
    roi_msg = "; roi=yes" if annotation_info and annotation_info.get("crops") else ("; roi=metadata" if annotation_info else "")
    log("info", f"Portal AI feedback saved: {body.label} ({outcome}); frame={'yes' if frame_info and frame_info.get('captured') else 'no'}{roi_msg}{sup_msg}{learn_msg}", "portal_ai", printer=printer_id, label=body.label, outcome=outcome, frame=(frame_info or {}).get("relative_path"))
    return {"ok": True, "feedback": row, "frame": frame_info, "annotation": annotation_info, "training": training_snapshot.get("training_use"), "interpretation": interpretation, "suppression": suppression, "learning": learning_result}


@app.post("/api/printers/{printer_id}/ai/feedback/reason")
async def api_ai_feedback_reason(printer_id: str, body: AIFeedbackReasonRequest):
    """Attach an optional reason chip/note to a previously saved feedback sample.

    The main feedback button saves immediately. This endpoint lets the UI add a
    lightweight reason afterward without blocking the original feedback flow.
    """
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(status_code=404, detail="Printer not configured")
    reason = str(body.reason or "").strip()[:240]
    reason_key = str(body.reason_key or "").strip()[:80]
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required")

    update_result: dict[str, Any] = {"ok": False, "updated": False, "enabled": False}
    ai_cfg = cfg.get("portal_ai", {}) or {}
    if bool(ai_cfg.get("ai_feedback_learning_enabled", True)):
        try:
            update_result = await asyncio.to_thread(
                ai_learning.update_feedback_reason,
                body.sample_id,
                printer_id,
                reason,
                reason_key,
                body.label,
                body.feedback_timestamp,
            )
            update_result["enabled"] = True
        except Exception as exc:
            update_result = {"ok": False, "updated": False, "enabled": True, "error": str(exc)}
            log("warning", f"AI feedback reason SQLite update failed: {exc}", "portal_ai", printer=printer_id)

    event = {
        "schema": "cc2-ai-feedback-reason-v1",
        "kind": "feedback_reason_update",
        "printer_id": printer_id,
        "label": str(body.label or "unknown"),
        "reason": reason,
        "reason_key": reason_key,
        "feedback_timestamp": body.feedback_timestamp,
        "learning_sample_id": body.sample_id,
        "timestamp": time.time(),
        "sqlite_update": update_result,
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "ai_feedback.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        event["persist_error"] = str(exc)
        log("warning", f"AI feedback reason JSONL append failed: {exc}", "portal_ai", printer=printer_id)

    log("info", f"Portal AI feedback reason saved: {body.label} -> {reason}", "portal_ai", printer=printer_id, label=body.label, reason_key=reason_key)
    return {"ok": True, "reason": {"text": reason, "key": reason_key}, "learning": update_result, "event": event}


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
    health = _connection_health_from_snapshot(client.snapshot())
    if not health.get("reachable"):
        raise HTTPException(409, f"Printer is {health.get('label', 'Offline')}: {health.get('reason', 'telemetry unavailable')}")
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


def _fan_percent_from_raw(raw: Any, *, assume_pwm: bool = True) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except Exception:
        return None
    if assume_pwm:
        if 0.0 <= value <= 1.0 and not float(value).is_integer():
            pct = value * 100.0
        else:
            pct = value / 255.0 * 100.0
    else:
        pct = value if value <= 100.0 else value / 255.0 * 100.0
    return int(max(0, min(100, round(pct))))


def _control_fan_percent(n: dict[str, Any], raw: dict[str, Any], *names: str) -> int:
    fans = n.get("fans") if isinstance(n, dict) else {}
    if isinstance(fans, dict):
        for name in names:
            fan = fans.get(name)
            if isinstance(fan, dict):
                pct = fan.get("percent")
                if pct is not None:
                    try:
                        return int(max(0, min(100, round(float(pct)))))
                    except Exception:
                        pass
                pct = _fan_percent_from_raw(fan.get("speed"))
                if pct is not None:
                    return pct
    mqtt_fans = _dig(raw, "fans", default={})
    if isinstance(mqtt_fans, dict):
        for name in names:
            candidates = {
                "model": ("fan", "ModelFan", "modelFan"),
                "auxiliary": ("aux_fan", "AuxiliaryFan", "auxiliaryFan", "auxFan", "sideFan"),
                "aux": ("aux_fan", "AuxiliaryFan", "auxiliaryFan", "auxFan", "sideFan"),
                "case": ("box_fan", "BoxFan", "boxFan", "CaseFan", "caseFan", "chassisFan"),
                "box": ("box_fan", "BoxFan", "boxFan", "CaseFan", "caseFan", "chassisFan"),
            }.get(str(name).lower(), (name,))
            for key in candidates:
                item = _dig(mqtt_fans, key, default=None)
                raw_speed = item.get("speed", item.get("Speed")) if isinstance(item, dict) else item
                pct = _fan_percent_from_raw(raw_speed, assume_pwm=True)
                if pct is not None:
                    return pct

    current = _dig(raw, "CurrentFanSpeed", "currentFanSpeed", default={})
    if isinstance(current, dict):
        for name in names:
            candidates = {
                "model": ("ModelFan", "modelFan", "ModeFan", "modeFan"),
                "auxiliary": ("AuxiliaryFan", "auxiliaryFan", "auxFan", "sideFan"),
                "aux": ("AuxiliaryFan", "auxiliaryFan", "auxFan", "sideFan"),
                "case": ("BoxFan", "boxFan", "CaseFan", "caseFan", "chassisFan"),
                "box": ("BoxFan", "boxFan", "CaseFan", "caseFan", "chassisFan"),
            }.get(str(name).lower(), (name,))
            for key in candidates:
                pct = _fan_percent_from_raw(_dig(current, key, default=None), assume_pwm=False)
                if pct is not None:
                    return pct
    return 0


def _control_position(n: dict[str, Any]) -> dict[str, Any]:
    pos = (n.get("position") or {}) if isinstance(n, dict) else {}

    def fmt(value: Any) -> str:
        if value in (None, ""):
            return "-"
        try:
            return f"{float(value):.1f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value)

    return {"x": fmt(pos.get("x")), "y": fmt(pos.get("y")), "z": fmt(pos.get("z"))}


def _control_speed_percent(n: dict[str, Any], raw: dict[str, Any]) -> int:
    pos = (n.get("position") or {}) if isinstance(n, dict) else {}
    for value in (pos.get("speed_percent"), _find_first_key(raw, "PrintSpeedPct", "print_speed_pct", max_depth=6)):
        try:
            if value not in (None, ""):
                return int(max(1, min(300, round(float(value)))))
        except Exception:
            pass
    mode = pos.get("speed_mode")
    try:
        mode_i = int(float(mode))
        return {0: 50, 1: 100, 2: 130, 3: 160}.get(mode_i, 100)
    except Exception:
        return 100


def _extract_light_on(n: dict[str, Any], raw: dict[str, Any]) -> bool:
    light_raw = _find_first_key(raw, "SecondLight", "LightStatus", "led", max_depth=6)
    light_on = bool((n.get("led") or {}).get("status")) if isinstance(n, dict) else False
    if isinstance(light_raw, dict):
        light_on = bool(_dig(light_raw, "SecondLight", "secondLight", "status", default=light_on))
    elif light_raw not in (None, ""):
        light_on = bool(light_raw)
    return light_on


def _control_temp_value(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except Exception:
            continue
        if number < -50 or number > 500:
            continue
        return round(number, 1)
    return None


def _control_temp_pair(normalized_obj: Any, raw_obj: Any, *, base_current: Any = None, base_target: Any = None, raw_current_keys: tuple[str, ...] = (), raw_target_keys: tuple[str, ...] = ()) -> dict[str, float | None]:
    norm = normalized_obj if isinstance(normalized_obj, dict) else {}
    rawd = raw_obj if isinstance(raw_obj, dict) else {}
    current_values: list[Any] = [base_current, norm.get("actual"), norm.get("current"), norm.get("temperature")]
    target_values: list[Any] = [base_target, norm.get("target")]
    for key in raw_current_keys:
        current_values.append(_dig(rawd, key, default=None))
    for key in raw_target_keys:
        target_values.append(_dig(rawd, key, default=None))
    return {
        "current": _control_temp_value(*current_values),
        "target": _control_temp_value(*target_values),
    }


def _control_temperatures(n: dict[str, Any], raw: dict[str, Any], base_status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    temps = (n.get("temps") or {}) if isinstance(n, dict) else {}
    nozzle = (temps.get("nozzle") or temps.get("extruder") or {}) if isinstance(temps, dict) else {}
    bed = (temps.get("bed") or temps.get("heater_bed") or {}) if isinstance(temps, dict) else {}
    chamber = (temps.get("chamber") or {}) if isinstance(temps, dict) else {}

    raw_extruder = _dig(raw, "extruder", "nozzle", "hotend", default={})
    raw_bed = _dig(raw, "heater_bed", "bed", default={})
    raw_chamber = _dig(raw, "ztemperature_sensor", "chamber", default={})

    extruder_pair = _control_temp_pair(
        nozzle,
        raw_extruder,
        base_current=base_status.get("hotend_current"),
        base_target=base_status.get("hotend_target"),
        raw_current_keys=("temperature", "actual", "current", "TempCurrentNozzle", "CurrentNozzleTemp"),
        raw_target_keys=("target", "target_temperature", "TempTargetNozzle", "TargetNozzleTemp"),
    )
    bed_pair = _control_temp_pair(
        bed,
        raw_bed,
        base_current=base_status.get("bed_current"),
        base_target=base_status.get("bed_target"),
        raw_current_keys=("temperature", "actual", "current", "TempCurrentHotbed", "CurrentHotbedTemp"),
        raw_target_keys=("target", "target_temperature", "TempTargetHotbed", "TargetHotbedTemp"),
    )
    chamber_pair = _control_temp_pair(
        chamber,
        raw_chamber,
        base_current=base_status.get("chamber_current"),
        base_target=base_status.get("chamber_target"),
        raw_current_keys=("temperature", "actual", "current", "TempCurrentChamber", "CurrentChamberTemp"),
        raw_target_keys=("target", "target_temperature", "TempTargetChamber", "TargetChamberTemp"),
    )
    return {
        "extruder": {**extruder_pair, "min": 0, "max": 350, "label": "Extruder"},
        "bed": {**bed_pair, "min": 0, "max": 110, "label": "Bed"},
        "chamber": {**chamber_pair, "min": 0, "max": 100, "label": "Chamber", "editable": False},
    }


def _control_status_payload(printer_id: str) -> dict[str, Any]:
    _raise_if_feature_locked("control_page_enabled")
    pdata = _require_printer_running(printer_id)
    snap = runtime.snapshot(printer_id) or {}
    n = (snap.get("normalized") or {}) if isinstance(snap, dict) else {}
    raw = (snap.get("raw_status") or {}) if isinstance(snap, dict) else {}
    health = _connection_health_from_snapshot(snap if isinstance(snap, dict) else None)
    connected = bool(health.get("reachable"))
    base_status = _status_from_snapshot(printer_id, pdata, snap, attach_ai=False) if isinstance(snap, dict) else {}
    state = str(base_status.get("status_text") or n.get("sub_state") or n.get("state") or base_status.get("state") or ("connected" if connected else "offline"))
    speed_pct = _control_speed_percent(n, raw)
    temperatures = _control_temperatures(n, raw, base_status)
    active_print = bool(base_status.get("active_print")) if connected else False
    phase = base_status.get("print_phase") if isinstance(base_status.get("print_phase"), dict) else _print_phase_from_status(base_status, snap)
    controls_locked = bool(active_print or not connected)
    if active_print:
        controls_locked_reason = "Control page commands are locked while a print job is active."
    elif not connected:
        controls_locked_reason = f"Control page commands are locked because the printer is {base_status.get('status_text') or health.get('label') or 'offline'}."
    else:
        controls_locked_reason = ""
    camera_relay = camera_relays.get(printer_id, printer_dict_to_config(printer_id, pdata)).status()
    return {
        "ok": True,
        "printer_id": printer_id,
        "name": pdata.get("name") or printer_id,
        "host": pdata.get("host"),
        "connected": connected,
        "connection_state": health.get("connection_state"),
        "connection_reason": health.get("reason"),
        "offline": bool(health.get("offline")),
        "stale": bool(health.get("stale")),
        "state": state,
        "status_text": base_status.get("status_text") or _nice_status(state),
        "status_code": base_status.get("status_code"),
        "sub_status_code": base_status.get("sub_status_code"),
        "print_phase": phase,
        "active_print": active_print,
        "controls_locked": controls_locked,
        "controls_locked_reason": controls_locked_reason,
        "allow_commands": bool(snap.get("allow_commands", pdata.get("allow_commands", True))),
        "allow_dangerous_commands": bool(snap.get("allow_dangerous_commands", pdata.get("allow_dangerous_commands", False))),
        "position": _control_position(n),
        "speed_percent": speed_pct,
        "speed_label": _speed_label(None, None, speed_pct),
        "speed_presets": [50, 100, 130, 160],
        "fans": {
            "model": _control_fan_percent(n, raw, "model", "ModelFan"),
            "auxiliary": _control_fan_percent(n, raw, "auxiliary", "aux", "AuxiliaryFan"),
            "case": _control_fan_percent(n, raw, "case", "box", "BoxFan"),
        },
        "temperatures": temperatures,
        "light_on": _extract_light_on(n, raw),
        "camera_url": f"/api/printers/{printer_id}/camera/stream",
        "camera_snapshot_url": f"/api/printers/{printer_id}/camera/snapshot.jpg",
        "camera_relay": camera_relay,
        "last_message_age_sec": snap.get("last_message_age_sec"),
    }


def _raise_if_control_locked(printer_id: str) -> dict[str, Any]:
    status = _control_status_payload(printer_id)
    if status.get("active_print"):
        raise HTTPException(409, "Control page commands are locked while a print job is active. Use the stock portal or pause/finish the print first.")
    if status.get("controls_locked") or not status.get("connected"):
        raise HTTPException(409, status.get("controls_locked_reason") or "Control page commands are locked because the printer is not online.")
    return status


@app.get("/api/printers/{printer_id}/control/status")
async def api_control_status(printer_id: str):
    return await asyncio.to_thread(_control_status_payload, printer_id)


@app.post("/api/printers/{printer_id}/control/fan")
async def api_control_fan(printer_id: str, body: ControlFanRequest):
    _raise_if_control_locked(printer_id)
    fan = str(body.fan or "").strip().lower().replace("-", "_")
    percent = int(max(0, min(100, body.percent)))
    params: dict[str, Any]
    label: str
    if fan in {"model", "part", "tool", "fan"}:
        params = fan_params(model=percent)
        label = "Model"
    elif fan in {"aux", "auxiliary", "assist", "assistance", "side"}:
        params = fan_params(aux=percent)
        label = "Auxiliary"
    elif fan in {"case", "box", "chassis", "chamber"}:
        params = fan_params(box=percent)
        label = "Case"
    else:
        raise HTTPException(400, "Unknown fan. Use model, auxiliary, or case.")
    result = await asyncio.to_thread(_send_command, printer_id, SET_FAN_SPEED, params, True, 12.0)
    log("info", f"Control fan {label.lower()} set to {percent}%", "command", printer=printer_id)
    return {"ok": True, "message": f"{label} fan set to {percent}%", "result": result.get("result")}



@app.post("/api/printers/{printer_id}/control/temperature")
async def api_control_temperature(printer_id: str, body: ControlTemperatureRequest):
    _raise_if_control_locked(printer_id)
    tool = str(body.tool or "").strip().lower().replace("-", "_")
    target = int(body.target)
    params: dict[str, Any]
    label: str
    max_target: int
    if tool in {"extruder", "nozzle", "hotend", "toolhead"}:
        max_target = 350
        if target > max_target:
            raise HTTPException(400, f"Extruder target must be between 0 and {max_target}°C.")
        params = temperature_params(nozzle=target)
        label = "Extruder"
    elif tool in {"bed", "hotbed", "heater_bed", "build_plate", "plate"}:
        max_target = 110
        if target > max_target:
            raise HTTPException(400, f"Bed target must be between 0 and {max_target}°C.")
        params = temperature_params(bed=target)
        label = "Bed"
    else:
        raise HTTPException(400, "Unknown temperature target. Use extruder or bed.")
    result = await asyncio.to_thread(_send_command, printer_id, SET_TEMPERATURE, params, True, 12.0)
    log("info", f"Control {label.lower()} temperature set to {target}°C", "command", printer=printer_id)
    message = f"{label} heater turned off" if target <= 0 else f"{label} temperature set to {target}°C"
    return {"ok": True, "message": message, "result": result.get("result")}

@app.post("/api/printers/{printer_id}/control/speed")
async def api_control_speed(printer_id: str, body: ControlSpeedRequest):
    _raise_if_control_locked(printer_id)
    percent = int(max(1, min(300, body.percent)))
    result = await asyncio.to_thread(_send_command, printer_id, SET_PRINT_SPEED, print_speed_pct_params(percent), True, 12.0)
    log("info", f"Control print speed set to {percent}%", "command", printer=printer_id)
    return {"ok": True, "message": f"Print speed set to {percent}%", "result": result.get("result")}


@app.post("/api/printers/{printer_id}/control/move")
async def api_control_move(printer_id: str, body: ControlMoveRequest):
    _raise_if_control_locked(printer_id)
    axis = str(body.axis or "").upper().strip()
    if axis not in {"X", "Y", "Z"}:
        raise HTTPException(400, "Axis must be X, Y, or Z.")
    step = float(body.step or 0)
    if step == 0:
        raise HTTPException(400, "Step cannot be zero.")
    result = await asyncio.to_thread(_send_command, printer_id, MOVE_AXES, move_axes_params(axis, step), True, 20.0)
    log("warning", f"Control moved {axis} by {step:g}mm", "command", printer=printer_id)
    return {"ok": True, "message": f"Moved {axis} by {step:g}mm", "result": result.get("result")}


@app.post("/api/printers/{printer_id}/control/home")
async def api_control_home(printer_id: str, body: ControlHomeRequest):
    _raise_if_control_locked(printer_id)
    axis = str(body.axis or "XYZ").upper().strip()
    if axis not in {"X", "Y", "Z", "XY", "XYZ"}:
        raise HTTPException(400, "Axis must be X, Y, Z, XY, or XYZ.")
    result = await asyncio.to_thread(_send_command, printer_id, HOME_AXES, home_axes_params(axis), True, 60.0)
    log("warning", f"Control homing requested for {axis}", "command", printer=printer_id)
    return {"ok": True, "message": f"Homing {axis}", "result": result.get("result")}


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
    _raise_if_feature_locked("filament_manager_enabled")
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
    _raise_if_feature_locked("filament_manager_enabled")
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
    _raise_if_feature_locked("filament_manager_enabled")
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
    _raise_if_feature_locked("filament_manager_enabled")
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
    _raise_if_feature_locked("filament_manager_enabled")
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
    _raise_if_feature_locked("filament_manager_enabled")
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
    _raise_if_feature_locked("filament_manager_enabled")
    result = await asyncio.to_thread(_send_command, printer_id, GET_MONO_FILAMENT_INFO, {}, True, 12.0, False)
    return result




GCODE_UPLOAD_CHUNK_SIZE = 1024 * 1024
GCODE_UPLOAD_MAX_RETRIES = 3
GCODE_UPLOAD_STORAGE_ENDPOINTS = {
    "local": "/upload",
    "u-disk": "/upload/udisk",
    "sd-card": "/upload/sdcard",
}


def _safe_gcode_upload_filename(raw_name: str | None) -> str:
    """Normalize browser-provided filenames before handing them to the printer.

    The stock portal sends only the basename in X-File-Name. Keep that behavior,
    reject path-like names, and limit uploads to sliced G-code files for now.
    """
    candidate = Path(str(raw_name or "upload.gcode").replace("\\", "/")).name.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ()[]{}+")
    cleaned = "".join(ch if ch in allowed else "_" for ch in candidate if ch not in "\r\n\t")
    cleaned = cleaned.strip(" ._") or "upload.gcode"
    if not cleaned.lower().endswith(".gcode"):
        raise HTTPException(400, "Only .gcode files can be uploaded.")
    if len(cleaned) > 180:
        stem = cleaned[:-6][:170].rstrip(" ._") or "upload"
        cleaned = f"{stem}.gcode"
    return cleaned


async def _write_upload_to_temp(upload: UploadFile, safe_name: str) -> tuple[Path, int, str, str]:
    upload_dir = DATA_DIR / "tmp_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
    md5_digest = hashlib.md5()
    sha256_digest = hashlib.sha256()
    total = 0
    try:
        with temp_path.open("wb") as out:
            while True:
                chunk = await upload.read(GCODE_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                md5_digest.update(chunk)
                sha256_digest.update(chunk)
                out.write(chunk)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Failed to stage upload: {exc}") from exc
    if total <= 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(400, "Uploaded file is empty.")
    return temp_path, total, md5_digest.hexdigest(), sha256_digest.hexdigest()


def _upload_reply_error(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("error_code", "ErrorCode", "code", "Code"):
        if key not in payload:
            continue
        code = payload.get(key)
        if code in (None, "", 0, "0", "000000"):
            return ""
        msg = payload.get("error_msg") or payload.get("ErrorMsg") or payload.get("message") or payload.get("Message") or payload.get("messages")
        return f"Printer returned {key} {code}{': ' + str(msg) if msg else ''}"
    if payload.get("success") is False:
        return f"Printer reported upload failure: {payload.get('messages') or payload.get('message') or payload}"
    return ""


async def _upload_file_to_printer_http(
    pcfg: Any,
    temp_path: Path,
    file_name: str,
    storage_media: str,
    file_md5: str,
    total_size: int,
) -> dict[str, Any]:
    endpoint = GCODE_UPLOAD_STORAGE_ENDPOINTS.get(storage_media)
    if not endpoint:
        raise HTTPException(400, "Upload storage must be local, u-disk, or sd-card.")
    target_url = f"http://{pcfg.host}{endpoint}"
    sent = 0
    chunks = 0
    last_payload: Any = {}
    timeout = httpx.Timeout(180.0, connect=8.0, read=180.0, write=180.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        with temp_path.open("rb") as fh:
            while sent < total_size:
                chunk = fh.read(min(GCODE_UPLOAD_CHUNK_SIZE, total_size - sent))
                if not chunk:
                    break
                start = sent
                end = sent + len(chunk)
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end - 1}/{total_size}",
                    "X-Token": pcfg.access_code or "",
                    "X-File-Name": quote(file_name, safe=""),
                    "X-File-MD5": file_md5,
                }
                last_error = ""
                for attempt in range(GCODE_UPLOAD_MAX_RETRIES + 1):
                    try:
                        response = await client.put(target_url, headers=headers, content=chunk)
                        text = response.text
                        if response.status_code >= 400:
                            raise RuntimeError(f"HTTP {response.status_code}: {text[:240]}")
                        try:
                            payload = response.json() if text else {"error_code": 0}
                        except Exception:
                            payload = {"error_code": 0, "raw": text[:240]}
                        err = _upload_reply_error(payload)
                        if err:
                            raise RuntimeError(err)
                        last_payload = payload
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        if attempt >= GCODE_UPLOAD_MAX_RETRIES:
                            raise HTTPException(502, f"Printer upload failed at byte {start}: {last_error}") from exc
                        await asyncio.sleep(0.25 * (attempt + 1))
                sent = end
                chunks += 1
    return {
        "error_code": 0,
        "bytes": sent,
        "chunks": chunks,
        "endpoint": endpoint,
        "printer_reply": last_payload,
    }


GCODE_STAGED_UPLOAD_DIR = DATA_DIR / "staged_gcode_uploads"
GCODE_STAGED_META_DIR = DATA_DIR / "staged_gcode_uploads" / "meta"
GCODE_STAGED_THUMB_DIR = DATA_DIR / "staged_gcode_uploads" / "thumbnails"
GCODE_STAGE_RETENTION_SECONDS = 7 * 24 * 60 * 60
GCODE_STAGE_RAW_METADATA_LIMIT = 120
GCODE_STAGE_SAMPLE_COMMAND_LIMIT = 10


def _stage_upload_id(value: str) -> str:
    upload_id = re.sub(r"[^a-fA-F0-9]", "", str(value or "")).lower()
    if len(upload_id) != 32:
        raise HTTPException(404, "Staged upload not found")
    return upload_id


def _ensure_stage_dirs() -> None:
    GCODE_STAGED_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    GCODE_STAGED_META_DIR.mkdir(parents=True, exist_ok=True)
    GCODE_STAGED_THUMB_DIR.mkdir(parents=True, exist_ok=True)


def _path_is_under(child: Path, parent: Path) -> bool:
    try:
        child_resolved = child.resolve()
        parent_resolved = parent.resolve()
        return str(child_resolved).startswith(str(parent_resolved) + os.sep) or child_resolved == parent_resolved
    except Exception:
        return False


def _meta_path_for_stage(upload_id: str) -> Path:
    return GCODE_STAGED_META_DIR / f"{_stage_upload_id(upload_id)}.json"


def _load_staged_upload(upload_id: str) -> dict[str, Any]:
    meta_path = _meta_path_for_stage(upload_id)
    if not meta_path.exists():
        raise HTTPException(404, "Staged upload not found")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(500, f"Could not read staged upload metadata: {exc}") from exc
    file_path = Path(str(meta.get("file_path") or ""))
    if not file_path.exists() or not _path_is_under(file_path, GCODE_STAGED_UPLOAD_DIR):
        raise HTTPException(404, "Staged G-code file is missing")
    meta["file_path"] = str(file_path)
    return meta


def _public_staged_upload(meta: dict[str, Any]) -> dict[str, Any]:
    public = dict(meta)
    upload_id = str(public.get("id") or "")
    public.pop("file_path", None)
    thumb_path = public.pop("thumbnail_path", "") or ""
    if thumb_path:
        public["thumbnail_url"] = f"/api/uploads/{upload_id}/thumbnail?v={int(public.get('uploaded_at_epoch') or time.time())}"
    else:
        public["thumbnail_url"] = None
    return public


def _clean_comment_key(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "").strip().strip(";# "))
    value = value.replace("_", " ").strip()
    return value[:96]


def _clean_metadata_value(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "").strip())
    return value[:500]


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _gcode_bounds_payload(bounds: dict[str, list[float]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for axis in ("x", "y", "z"):
        vals = bounds.get(axis) or []
        if not vals:
            continue
        lo = min(vals)
        hi = max(vals)
        out[f"{axis}_min"] = round(lo, 3)
        out[f"{axis}_max"] = round(hi, 3)
        out[{"x": "width", "y": "depth", "z": "height"}[axis]] = round(hi - lo, 3)
    return out


def _maybe_store_comment_metadata(raw: dict[str, str], key: str, value: str) -> None:
    key = _clean_comment_key(key)
    value = _clean_metadata_value(value)
    if not key or not value or len(raw) >= GCODE_STAGE_RAW_METADATA_LIMIT:
        return
    low_value = value.lower()
    # Thumbnail base64 lines and long opaque blobs are not useful in the UI.
    if len(value) > 180 and re.fullmatch(r"[A-Za-z0-9+/= ]+", value):
        return
    if key not in raw:
        raw[key] = value


def _update_known_gcode_fields(fields: dict[str, Any], key: str, value: str) -> None:
    low = _clean_comment_key(key).lower()
    val = _clean_metadata_value(value)
    val_num = _float_or_none(val.split()[0] if val else None)
    if not val:
        return
    if "estimated" in low and "time" in low:
        fields.setdefault("estimated_print_time", val)
    elif low in {"time", "print time"} or low.endswith("print time"):
        fields.setdefault("estimated_print_time", val)
    elif "filament used" in low or low in {"filament", "filament length"}:
        bucket = fields.setdefault("filament", {})
        if "g" in low and val_num is not None:
            bucket.setdefault("used_g", val)
        elif "mm" in low and val_num is not None:
            bucket.setdefault("used_mm", val)
        elif "m" in low and val_num is not None:
            bucket.setdefault("used_m", val)
        elif "cm3" in low or "volume" in low:
            bucket.setdefault("used_volume", val)
        else:
            bucket.setdefault("used", val)
    elif "filament type" in low or low == "filament_type":
        fields.setdefault("filament", {}).setdefault("type", val)
    elif "filament colour" in low or "filament color" in low:
        fields.setdefault("filament", {}).setdefault("color", val)
    elif "filament settings id" in low or "filament preset" in low:
        fields.setdefault("filament", {}).setdefault("preset", val)
    elif "layer height" in low and "first" not in low:
        fields.setdefault("layer_height", val)
    elif "first layer height" in low:
        fields.setdefault("first_layer_height", val)
    elif "nozzle" in low and "diameter" in low:
        fields.setdefault("nozzle_diameter", val)
    elif "bed" in low and ("temperature" in low or low.endswith("temp")):
        fields.setdefault("temperatures", {}).setdefault("bed", val)
    elif ("temperature" in low or low.endswith("temp")) and "bed" not in low:
        fields.setdefault("temperatures", {}).setdefault("nozzle", val)
    elif "g-code flavor" in low or "gcode flavor" in low:
        fields.setdefault("gcode_flavor", val)
    elif "slicer" in low or "generated by" in low:
        fields.setdefault("slicer", val)
    elif "total layer" in low or low in {"layer count", "layer_count"}:
        if val_num is not None:
            fields.setdefault("layers", {})["total"] = int(val_num)


def _decode_thumbnail_block(block: dict[str, Any]) -> tuple[bytes | None, str | None]:
    encoded = "".join(block.get("lines") or [])
    if not encoded:
        return None, None
    try:
        data = base64.b64decode("".join(encoded.split()), validate=False)
    except Exception:
        return None, None
    media = _looks_like_image_bytes(data)
    if not media:
        marker = str(block.get("marker") or "").lower()
        if "jpg" in marker or "jpeg" in marker:
            media = "image/jpeg"
        elif "png" in marker:
            media = "image/png"
    if not media:
        return None, None
    return data, media


def _analyze_staged_gcode(file_path: Path, upload_id: str, safe_name: str, size: int, md5: str, sha256: str) -> dict[str, Any]:
    raw_metadata: dict[str, str] = {}
    known_fields: dict[str, Any] = {}
    bounds: dict[str, list[float]] = {"x": [], "y": [], "z": []}
    first_commands: list[str] = []
    last_commands: list[str] = []
    line_count = 0
    comment_lines = 0
    command_lines = 0
    tool_changes = 0
    max_layer_index: int | None = None
    thumbnail_blocks: list[dict[str, Any]] = []
    current_thumb: dict[str, Any] | None = None
    absolute_xyz = True
    current_pos: dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}
    token_re = re.compile(r"([XYZEFS])\s*(-?\d+(?:\.\d+)?)", re.I)
    thumb_begin_re = re.compile(r"^\s*;\s*(thumbnail(?:_[a-z0-9]+)?)\s+begin\s+(\d+)x(\d+)(?:\s+(\d+))?", re.I)
    thumb_end_re = re.compile(r"^\s*;\s*thumbnail(?:_[a-z0-9]+)?\s+end", re.I)

    with file_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line_count += 1
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()

            if current_thumb is not None:
                if thumb_end_re.match(stripped):
                    thumbnail_blocks.append(current_thumb)
                    current_thumb = None
                else:
                    data_line = stripped.lstrip(";").strip()
                    if data_line:
                        current_thumb.setdefault("lines", []).append(data_line)
                continue

            begin = thumb_begin_re.match(stripped)
            if begin:
                current_thumb = {
                    "marker": begin.group(1),
                    "width": int(begin.group(2)),
                    "height": int(begin.group(3)),
                    "declared_size": int(begin.group(4) or 0),
                    "lines": [],
                }
                comment_lines += 1
                continue

            if stripped.startswith(";"):
                comment_lines += 1
                comment = stripped[1:].strip()
                if comment:
                    if comment.lower().startswith("generated by"):
                        known_fields.setdefault("slicer", comment)
                        _maybe_store_comment_metadata(raw_metadata, "Generated by", comment)
                    kv = re.match(r"([^:=]{2,120})\s*[:=]\s*(.+)$", comment)
                    if kv:
                        key, value = kv.group(1), kv.group(2)
                        _maybe_store_comment_metadata(raw_metadata, key, value)
                        _update_known_gcode_fields(known_fields, key, value)
                    layer_match = re.match(r"LAYER\s*[:=]\s*(-?\d+)", comment, re.I)
                    if layer_match:
                        try:
                            max_layer_index = max(max_layer_index if max_layer_index is not None else -1, int(layer_match.group(1)))
                        except Exception:
                            pass
                    layer_count = re.match(r"(?:LAYER_COUNT|total layers? count|total layers?)\s*[:=]\s*(\d+)", comment, re.I)
                    if layer_count:
                        known_fields.setdefault("layers", {})["total"] = int(layer_count.group(1))
                continue

            code = line.split(";", 1)[0].strip()
            if not code:
                continue
            command_lines += 1
            compact = code[:160]
            if len(first_commands) < GCODE_STAGE_SAMPLE_COMMAND_LIMIT:
                first_commands.append(compact)
            last_commands.append(compact)
            if len(last_commands) > GCODE_STAGE_SAMPLE_COMMAND_LIMIT:
                last_commands.pop(0)
            upper = code.upper()
            if upper.startswith("G90"):
                absolute_xyz = True
            elif upper.startswith("G91"):
                absolute_xyz = False
            elif upper.startswith("T") and re.match(r"^T\d+\b", upper):
                tool_changes += 1
            if upper.startswith(("G0", "G1")):
                seen_axis: dict[str, float] = {}
                for axis, raw_value in token_re.findall(code):
                    axis_l = axis.lower()
                    if axis_l not in {"x", "y", "z"}:
                        continue
                    try:
                        seen_axis[axis_l] = float(raw_value)
                    except Exception:
                        pass
                for axis, value in seen_axis.items():
                    pos = value if absolute_xyz else current_pos.get(axis, 0.0) + value
                    current_pos[axis] = pos
                    bounds.setdefault(axis, []).append(pos)

    if current_thumb is not None:
        thumbnail_blocks.append(current_thumb)

    thumbnail_path = ""
    thumbnail_info: dict[str, Any] | None = None
    if thumbnail_blocks:
        thumbnail_blocks.sort(key=lambda b: (int(b.get("width") or 0) * int(b.get("height") or 0), int(b.get("declared_size") or 0)), reverse=True)
        for block in thumbnail_blocks:
            data, media = _decode_thumbnail_block(block)
            if not data or not media:
                continue
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(media, ".img")
            GCODE_STAGED_THUMB_DIR.mkdir(parents=True, exist_ok=True)
            path = GCODE_STAGED_THUMB_DIR / f"{upload_id}{ext}"
            path.write_bytes(data)
            thumbnail_path = str(path)
            thumbnail_info = {
                "width": block.get("width"),
                "height": block.get("height"),
                "media_type": media,
                "bytes": len(data),
                "count_found": len(thumbnail_blocks),
            }
            break

    if max_layer_index is not None:
        known_fields.setdefault("layers", {})["detected_max_index"] = max_layer_index
        known_fields.setdefault("layers", {}).setdefault("estimated_total_from_indices", max_layer_index + 1)

    dimensions = _gcode_bounds_payload(bounds)
    uploaded_at_epoch = time.time()
    return {
        "id": upload_id,
        "file_name": safe_name,
        "size": size,
        "md5": md5,
        "sha256": sha256,
        "uploaded_at_epoch": uploaded_at_epoch,
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(uploaded_at_epoch)),
        "file_path": str(file_path),
        "thumbnail_path": thumbnail_path,
        "thumbnail": thumbnail_info,
        "summary": {
            "line_count": line_count,
            "comment_lines": comment_lines,
            "command_lines": command_lines,
            "tool_changes": tool_changes,
            "has_thumbnail": bool(thumbnail_path),
        },
        "known_fields": known_fields,
        "dimensions": dimensions,
        "raw_metadata": raw_metadata,
        "first_commands": first_commands,
        "last_commands": last_commands,
    }


def _save_staged_upload(meta: dict[str, Any]) -> None:
    _ensure_stage_dirs()
    path = _meta_path_for_stage(str(meta.get("id")))
    path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def _cleanup_old_staged_uploads() -> None:
    try:
        _ensure_stage_dirs()
        cutoff = time.time() - GCODE_STAGE_RETENTION_SECONDS
        for meta_path in GCODE_STAGED_META_DIR.glob("*.json"):
            if meta_path.stat().st_mtime >= cutoff:
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            for raw in (meta.get("file_path"), meta.get("thumbnail_path")):
                if not raw:
                    continue
                path = Path(str(raw))
                if _path_is_under(path, GCODE_STAGED_UPLOAD_DIR):
                    path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
    except Exception as exc:
        log("debug", f"Staged upload cleanup skipped: {exc}", "files")


@app.post("/api/uploads/stage")
async def api_stage_upload(file: UploadFile = File(...)):
    _cleanup_old_staged_uploads()
    safe_name = _safe_gcode_upload_filename(file.filename)
    temp_path, total_size, file_md5, file_sha256 = await _write_upload_to_temp(file, safe_name)
    _ensure_stage_dirs()
    upload_id = uuid.uuid4().hex
    staged_path = GCODE_STAGED_UPLOAD_DIR / f"{upload_id}_{safe_name}"
    try:
        shutil.move(str(temp_path), staged_path)
        meta = await asyncio.to_thread(_analyze_staged_gcode, staged_path, upload_id, safe_name, total_size, file_md5, file_sha256)
        _save_staged_upload(meta)
        log("info", f"Staged {safe_name} ({total_size} bytes) for upload review", "files")
        return {"ok": True, "upload": _public_staged_upload(meta)}
    except HTTPException:
        staged_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        staged_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Failed to inspect staged G-code: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


@app.get("/api/uploads/{upload_id}")
async def api_get_staged_upload(upload_id: str):
    meta = _load_staged_upload(upload_id)
    return {"ok": True, "upload": _public_staged_upload(meta)}


@app.get("/api/uploads/{upload_id}/thumbnail")
async def api_staged_upload_thumbnail(upload_id: str):
    meta = _load_staged_upload(upload_id)
    thumb_path = Path(str(meta.get("thumbnail_path") or ""))
    if not thumb_path.exists() or not _path_is_under(thumb_path, GCODE_STAGED_UPLOAD_DIR):
        raise HTTPException(404, "No thumbnail found in this G-code file")
    media = _looks_like_image_bytes(thumb_path.read_bytes()[:32]) or "image/png"
    return FileResponse(thumb_path, media_type=media, headers={"Cache-Control": "no-store"})


@app.post("/api/printers/{printer_id}/uploads/{upload_id}/send")
async def api_send_staged_upload(printer_id: str, upload_id: str, body: StagedUploadSendRequest | None = None):
    body = body or StagedUploadSendRequest()
    cfg = load_config()
    pdata = (cfg.get("printers") or {}).get(printer_id)
    if not pdata:
        raise HTTPException(404, "Printer not configured")
    pcfg = printer_dict_to_config(printer_id, pdata)
    if not pcfg.allow_commands:
        raise HTTPException(403, "Uploads are blocked by safety settings. Enable allow_commands for this printer first.")
    media = normalize_storage_media(body.storage_media)
    if media not in GCODE_UPLOAD_STORAGE_ENDPOINTS:
        raise HTTPException(400, "Upload storage must be local, u-disk, or sd-card.")
    meta = _load_staged_upload(upload_id)
    file_path = Path(str(meta.get("file_path")))
    safe_name = _safe_gcode_upload_filename(str(meta.get("file_name") or file_path.name))
    total_size = int(meta.get("size") or file_path.stat().st_size)
    file_md5 = str(meta.get("md5") or "")
    if not file_md5:
        file_md5 = hashlib.md5(file_path.read_bytes()).hexdigest()
    upload_result = await _upload_file_to_printer_http(pcfg, file_path, safe_name, media, file_md5, total_size)
    start_result: dict[str, Any] | None = None
    print_error = ""
    if body.print_after:
        try:
            start_result = await asyncio.to_thread(
                _send_command,
                printer_id,
                START_PRINT,
                start_print_params(safe_name, media, body.start_layer, body.calibration, body.platform_type, body.timelapse),
                True,
                20.0,
                True,
            )
        except HTTPException as exc:
            print_error = str(exc.detail)
        except Exception as exc:
            print_error = str(exc)
    log(
        "info",
        f"Sent staged upload {safe_name} ({total_size} bytes) to {media}{' and requested print start' if body.print_after and not print_error else ''}",
        "files",
        printer=printer_id,
    )
    return {
        "ok": True,
        "file_name": safe_name,
        "storage_media": media,
        "size": total_size,
        "md5": file_md5,
        "upload_result": upload_result,
        "printed": bool(body.print_after and not print_error),
        "print_error": print_error,
        "start_result": start_result,
        "staged_upload": _public_staged_upload(meta),
    }


@app.get("/api/printers/{printer_id}/files")
async def api_files(printer_id: str, path: str = "/", storage_media: str = "local", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), offset: Optional[int] = None, limit: Optional[int] = None):
    _raise_if_feature_locked("file_manager_enabled")
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




@app.post("/api/printers/{printer_id}/files/upload")
async def api_file_upload(
    printer_id: str,
    file: UploadFile = File(...),
    storage_media: str = Form("local"),
    print_after: bool = Form(False),
    start_layer: int = Form(0),
    calibration: bool = Form(False),
    platform_type: int = Form(0),
    timelapse: bool = Form(False),
):
    _raise_if_feature_locked("file_manager_enabled")
    cfg = load_config()
    pdata = (cfg.get("printers") or {}).get(printer_id)
    if not pdata:
        raise HTTPException(404, "Printer not configured")
    pcfg = printer_dict_to_config(printer_id, pdata)
    if not pcfg.allow_commands:
        raise HTTPException(403, "Uploads are blocked by safety settings. Enable allow_commands for this printer first.")
    media = normalize_storage_media(storage_media)
    if media not in GCODE_UPLOAD_STORAGE_ENDPOINTS:
        raise HTTPException(400, "Upload storage must be local, u-disk, or sd-card.")
    safe_name = _safe_gcode_upload_filename(file.filename)
    temp_path, total_size, file_md5, _file_sha256 = await _write_upload_to_temp(file, safe_name)
    try:
        upload_result = await _upload_file_to_printer_http(pcfg, temp_path, safe_name, media, file_md5, total_size)
        start_result: dict[str, Any] | None = None
        print_error = ""
        if print_after:
            try:
                start_result = await asyncio.to_thread(
                    _send_command,
                    printer_id,
                    START_PRINT,
                    start_print_params(safe_name, media, start_layer, calibration, platform_type, timelapse),
                    True,
                    20.0,
                    True,
                )
            except HTTPException as exc:
                print_error = str(exc.detail)
            except Exception as exc:
                print_error = str(exc)
        log(
            "info",
            f"Uploaded {safe_name} ({total_size} bytes) to {media}{' and requested print start' if print_after and not print_error else ''}",
            "files",
            printer=printer_id,
        )
        return {
            "ok": True,
            "file_name": safe_name,
            "storage_media": media,
            "size": total_size,
            "md5": file_md5,
            "upload_result": upload_result,
            "printed": bool(print_after and not print_error),
            "print_error": print_error,
            "start_result": start_result,
        }
    finally:
        temp_path.unlink(missing_ok=True)


@app.get("/api/printers/{printer_id}/files/detail")
async def api_file_detail(printer_id: str, filename: str, storage_media: str = "local", directory: Optional[str] = None):
    _raise_if_feature_locked("file_manager_enabled")
    return await asyncio.to_thread(_send_command, printer_id, GET_FILE_DETAIL, file_detail_params(filename, storage_media, directory), True, 15.0)


@app.get("/api/printers/{printer_id}/files/thumbnail")
async def api_file_thumbnail(printer_id: str, filename: str, storage_media: str = "local"):
    _raise_if_feature_locked("file_manager_enabled")
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


def _delete_file_command(printer_id: str, file_path: str, storage_media: str) -> dict[str, Any]:
    """Delete a printer/USB file using the stock portal payload shape.

    The stock Elegoo UI sends method 1047 with ``file_path`` as an array,
    even for a single selected file.  Firmware may reject the older string
    shape with 1003, most visibly for local Printer Files.  Try the stock
    array payload first and keep the old string payload as a cautious fallback
    for any firmware build that still expects it.
    """
    media = normalize_storage_media(storage_media)
    primary_params = delete_file_params(file_path, media)
    try:
        return _send_command(printer_id, DELETE_FILE, primary_params, True, 15.0)
    except HTTPException as exc:
        detail = str(exc.detail or exc)
        if "1003" not in detail and "Invalid parameter" not in detail:
            raise
        legacy_params = delete_file_params_legacy(file_path, media)
        log(
            "warning",
            f"Delete file array payload was rejected; retrying legacy string payload for {file_path}",
            "files",
            printer=printer_id,
            raw={"primary_params": primary_params, "legacy_params": legacy_params, "error": detail},
        )
        return _send_command(printer_id, DELETE_FILE, legacy_params, True, 15.0)


@app.post("/api/printers/{printer_id}/files/delete")
async def api_file_delete(printer_id: str, body: DeleteFileRequest):
    _raise_if_feature_locked("file_manager_enabled")
    return await asyncio.to_thread(_delete_file_command, printer_id, body.file_path, body.storage_media)


@app.post("/api/printers/{printer_id}/files/start")
async def api_file_start(printer_id: str, body: StartPrintRequest):
    _raise_if_feature_locked("file_manager_enabled")
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
    _raise_if_feature_locked("file_manager_enabled")
    return await asyncio.to_thread(_send_command, printer_id, GET_DISK_INFO, {"storage_media": normalize_storage_media(storage_media)}, True, 10.0)


@app.get("/api/printers/{printer_id}/canvas")
async def api_canvas(printer_id: str):
    _raise_if_feature_locked("filament_manager_enabled")
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


def _cleanup_timelapse_export_jobs(now: float | None = None) -> None:
    now = float(now or time.time())
    with _TIMELAPSE_EXPORT_LOCK:
        for job_id, job in list(_TIMELAPSE_EXPORT_JOBS.items()):
            updated = float(job.get("updated_at") or job.get("started_at") or now)
            status = str(job.get("status") or "")
            if now - updated > _TIMELAPSE_EXPORT_JOB_TTL_SEC and status != "generating":
                _TIMELAPSE_EXPORT_JOBS.pop(job_id, None)
            elif status == "generating" and now - float(job.get("started_at") or now) > _TIMELAPSE_EXPORT_TIMEOUT_SEC + 300:
                job.update({
                    "status": "error",
                    "message": "Time-lapse export timed out. Refresh Video List; the printer may still finish it firmware-side.",
                    "error": "export job exceeded backend timeout",
                    "updated_at": now,
                    "finished_at": now,
                })


def _public_timelapse_export_job(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {}
    allowed = {
        "id", "printer_id", "task_id", "task_name", "status", "message", "error",
        "started_at", "updated_at", "finished_at", "download_file_name", "download_url",
        "direct_download_url", "token", "elapsed_sec", "confirmed_by",
        "last_video_status", "source_record",
    }
    return {key: job.get(key) for key in allowed if key in job}


def _update_timelapse_export_job(job_id: str, **updates: Any) -> dict[str, Any] | None:
    with _TIMELAPSE_EXPORT_LOCK:
        job = _TIMELAPSE_EXPORT_JOBS.get(job_id)
        if not job:
            return None
        job.update(updates)
        job["updated_at"] = time.time()
        return dict(job)


def _decorate_timelapse_export_result(data: dict[str, Any], pcfg: Any, token: str) -> dict[str, Any]:
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


def _load_timelapse_videos_sync(printer_id: str, pcfg: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load and normalize the stock-style Video List from print history.

    The printer marks timelapse rows as status 1 while the captured video still
    needs to be generated/exported, then status 2 once the MP4 is actually ready.
    Export jobs use this as the source of truth instead of trusting the immediate
    1051 command response, because firmware may acknowledge the export request
    before generation is finished.
    """
    history_payload = _send_command(printer_id, GET_HISTORY_TASK, {}, True, 20.0, False)
    root = _unwrap_command_payload(history_payload)
    if isinstance(root, dict) and _as_int(root.get("error_code") or root.get("ErrorCode"), 0) != 0:
        return [], {"raw_history_total": 0, "raw_detail_total": 0}

    history_items = _extract_history_items(root)
    videos = [v for v in (_normalize_timelapse_record(item, pcfg) for item in history_items) if v]
    detail_items: list[Any] = []

    if not videos and history_items:
        ids: list[Any] = []
        for item in history_items[:80]:
            if isinstance(item, dict):
                ids.append(_field(item, "task_id", "TaskId", "taskId", "id", "Id"))
            else:
                ids.append(item)
        detail_items = _try_history_details(printer_id, ids)
        videos = [v for v in (_normalize_timelapse_record(item, pcfg) for item in detail_items) if v]

    return videos, {"raw_history_total": len(history_items), "raw_detail_total": len(detail_items)}


def _normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _timelapse_record_matches(record: dict[str, Any], *, task_id: Any = None, task_name: Any = None, token: str = "") -> bool:
    wanted_task = str(task_id).strip() if task_id not in (None, "") else ""
    record_task = str(record.get("task_id") or "").strip()
    if wanted_task and record_task and wanted_task == record_task:
        return True

    token_file = _download_file_name_from_token(token)
    record_token = str(record.get("time_lapse_video_url") or "")
    record_file = str(record.get("download_file_name") or "")
    record_file_from_token = _download_file_name_from_token(record_token or record_file)
    if token_file and token_file in {record_token, record_file, record_file_from_token}:
        return True

    wanted_name = _normalized_text(task_name)
    record_name = _normalized_text(record.get("task_name") or record.get("filename"))
    return bool(wanted_name and record_name and wanted_name == record_name)


def _find_timelapse_record(videos: list[dict[str, Any]], *, task_id: Any = None, task_name: Any = None, token: str = "") -> dict[str, Any] | None:
    for record in videos:
        if _timelapse_record_matches(record, task_id=task_id, task_name=task_name, token=token):
            return record
    return None


def _timelapse_status_message(record: dict[str, Any] | None, elapsed: int) -> str:
    if not record:
        return f"Time-lapse export requested; waiting for the printer Video List to refresh… ({seconds_to_hms(elapsed) or str(elapsed) + 's'})"
    status = _as_int(record.get("time_lapse_video_status"), 0)
    if bool(record.get("export_ready")):
        return "Time-lapse video ready to download."
    if status == 1:
        return f"Time-lapse video generating… ({seconds_to_hms(elapsed) or str(elapsed) + 's'})"
    if status == 3:
        return f"Printer reports timelapse export is still processing… ({seconds_to_hms(elapsed) or str(elapsed) + 's'})"
    return f"Waiting for printer to mark the time-lapse as generated… ({seconds_to_hms(elapsed) or str(elapsed) + 's'})"


def _wait_for_timelapse_export_ready(job_id: str, pcfg: Any, *, task_id: Any = None, task_name: Any = None, token: str = "") -> dict[str, Any]:
    """Poll printer history until the selected timelapse is truly downloadable."""
    started = time.time()
    deadline = started + float(_TIMELAPSE_EXPORT_TIMEOUT_SEC)
    last_error = ""
    last_record: dict[str, Any] | None = None
    poll_interval = 5.0

    while time.time() < deadline:
        elapsed = int(time.time() - started)
        try:
            videos, _counts = _load_timelapse_videos_sync(pcfg.id, pcfg)
            record = _find_timelapse_record(videos, task_id=task_id, task_name=task_name, token=token)
            if record:
                last_record = record
                status = _as_int(record.get("time_lapse_video_status"), 0)
                _update_timelapse_export_job(
                    job_id,
                    status="generating",
                    message=_timelapse_status_message(record, elapsed),
                    elapsed_sec=elapsed,
                    last_video_status=status,
                    download_file_name=str(record.get("download_file_name") or _download_file_name_from_token(token) or ""),
                )
                if bool(record.get("export_ready")) and record.get("download_url"):
                    return record
            else:
                _update_timelapse_export_job(
                    job_id,
                    status="generating",
                    message=_timelapse_status_message(None, elapsed),
                    elapsed_sec=elapsed,
                )
        except Exception as exc:
            last_error = str(exc)
            _update_timelapse_export_job(
                job_id,
                status="generating",
                message=f"Time-lapse export requested; waiting for printer status… ({seconds_to_hms(elapsed) or str(elapsed) + 's'})",
                elapsed_sec=elapsed,
                error=last_error,
            )
        time.sleep(poll_interval)

    if last_record:
        raise TimeoutError(
            f"Timed out waiting for the printer to mark the timelapse as generated "
            f"(last video status {last_record.get('time_lapse_video_status')})."
        )
    if last_error:
        raise TimeoutError(f"Timed out waiting for timelapse generation; last status error: {last_error}")
    raise TimeoutError("Timed out waiting for the printer Video List to confirm the generated timelapse.")


def _run_timelapse_export_job(job_id: str) -> None:
    with _TIMELAPSE_EXPORT_LOCK:
        job = dict(_TIMELAPSE_EXPORT_JOBS.get(job_id) or {})
    if not job:
        return
    printer_id = str(job.get("printer_id") or "")
    token = str(job.get("token") or "").strip()
    task_id = job.get("task_id")
    task_name = job.get("task_name")
    if not printer_id or not token:
        _update_timelapse_export_job(job_id, status="error", message="Time-lapse export could not start: missing printer or video token.", error="missing printer_id/token", finished_at=time.time())
        return
    pcfg = _portal_target(printer_id)
    if not pcfg:
        _update_timelapse_export_job(job_id, status="error", message="Time-lapse export could not start: printer is not configured.", error="printer not configured", finished_at=time.time())
        return

    started = time.time()
    try:
        _update_timelapse_export_job(job_id, status="generating", message="Time-lapse video generating…", elapsed_sec=0)
        log("info", "Time-lapse export started", "files", printer=printer_id)

        command_data: dict[str, Any] = {}
        try:
            # Method 1051 may only acknowledge/start the firmware export. Do not
            # treat this reply as completion; the history Video List is polled
            # below until it reports the row as generated/downloadable.
            command_data = _send_command(
                printer_id,
                GET_TIME_LAPSE_VIDEO_LIST,
                timelapse_export_params(token),
                True,
                45.0,
                True,
            )
        except Exception as exc:
            # Some firmware builds hold the request open while generation starts.
            # If that initial request times out, keep watching the Video List
            # instead of instantly failing; non-timeout errors still stop the job.
            msg = str(exc)
            if "timeout" not in msg.lower() and "timed out" not in msg.lower():
                raise
            _update_timelapse_export_job(
                job_id,
                status="generating",
                message="Export request is taking a while; watching printer Video List for completion…",
                error=msg,
                elapsed_sec=int(time.time() - started),
            )
            log("warning", f"Time-lapse export command timed out; polling for completion anyway: {exc}", "files", printer=printer_id)

        if command_data:
            decorated = _decorate_timelapse_export_result(dict(command_data), pcfg, token)
            _update_timelapse_export_job(
                job_id,
                status="generating",
                message="Export accepted; waiting for printer to finish generating the time-lapse…",
                result=decorated,
                download_file_name=str(decorated.get("download_file_name") or _download_file_name_from_token(token) or ""),
                # Keep the derived URL out of the public job until the Video List
                # confirms status 2/export_ready. The token alone is not proof.
                download_url="",
                direct_download_url="",
                elapsed_sec=int(time.time() - started),
            )

        ready_record = _wait_for_timelapse_export_ready(
            job_id,
            pcfg,
            task_id=task_id,
            task_name=task_name,
            token=token,
        )
        download_file_name = str(ready_record.get("download_file_name") or _download_file_name_from_token(token) or "")
        download_url = str(ready_record.get("download_url") or (_timelapse_proxy_download_url(pcfg.id, download_file_name) if download_file_name else ""))
        direct_download_url = str(ready_record.get("direct_download_url") or (_stock_download_url(pcfg, download_file_name) if download_file_name else ""))
        message = "Time-lapse video ready to download."
        _update_timelapse_export_job(
            job_id,
            status="ready",
            message=message,
            source_record=ready_record,
            download_file_name=download_file_name,
            download_url=download_url,
            direct_download_url=direct_download_url,
            elapsed_sec=int(time.time() - started),
            confirmed_by="video_list_status_2",
            last_video_status=_as_int(ready_record.get("time_lapse_video_status"), 0),
            finished_at=time.time(),
            error="",
        )
        log("info", message, "files", printer=printer_id)
    except Exception as exc:
        _update_timelapse_export_job(
            job_id,
            status="error",
            message="Time-lapse export did not finish before the backend timeout. Refresh Video List; the printer may still finish it firmware-side.",
            error=str(exc),
            elapsed_sec=int(time.time() - started),
            finished_at=time.time(),
        )
        log("warning", f"Time-lapse export failed: {exc}", "files", printer=printer_id)


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
    # Status 1 behaves like the stock portal's "captured but not exported" state: the
    # row may include a TimeLapseVideoUrl token, but the actual MP4 download is not
    # available until method 1051 finishes generating/exporting it. Do not expose a
    # cc2-dash download button for that state, or the browser waits on a file that
    # does not exist yet.
    download_ready = bool(download_file_name) and (
        status == 2
        or (status not in (1, 3) and (bool(url) or _as_float(size, 0) > 0 or _as_float(duration, 0) > 0))
    )
    return {
        "task_id": task_id,
        "task_name": name or f"Task {task_id}",
        "begin_time": begin,
        "end_time": end,
        "task_status": _field(item, "task_status", "TaskStatus", "status", "Status", default=""),
        "time_lapse_video_status": status,
        "time_lapse_video_url": url,
        "download_file_name": download_file_name,
        "download_url": _timelapse_proxy_download_url(pcfg.id, download_file_name) if download_ready else "",
        "direct_download_url": _stock_download_url(pcfg, download_file_name) if download_ready else "",
        "export_ready": download_ready,
        "needs_export": bool(status == 1 and download_file_name),
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
    _raise_if_feature_locked("file_manager_enabled")
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
    _raise_if_feature_locked("file_manager_enabled")
    return await asyncio.to_thread(_send_command, printer_id, GET_HISTORY_TASK, {}, True, 20.0, False)


@app.get("/api/printers/{printer_id}/timelapse")
async def api_timelapse(printer_id: str):
    _raise_if_feature_locked("file_manager_enabled")
    # The stock Elegoo portal's "Video List" is derived from Print History, not
    # the file-list endpoint. It filters history rows where TimeLapseVideoStatus
    # is 1 (captured but not generated) or 2 (generated), then export/downloads
    # with method 1051 when needed.
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")

    videos, counts = await asyncio.to_thread(_load_timelapse_videos_sync, printer_id, pcfg)
    result = {
        "error_code": 0,
        "total": len(videos),
        "raw_history_total": int(counts.get("raw_history_total") or 0),
        "raw_detail_total": int(counts.get("raw_detail_total") or 0),
        "videos": videos,
    }
    return {"ok": True, "result": result, "videos": videos, "total": len(videos)}


@app.get("/api/printers/{printer_id}/timelapse/download")
async def api_timelapse_download(printer_id: str, file_name: str = Query(..., min_length=1), media: str = Query("local")):
    _raise_if_feature_locked("file_manager_enabled")
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
    _raise_if_feature_locked("file_manager_enabled")
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    token = str(body.url or "").strip()
    if not token:
        raise HTTPException(400, "Missing timelapse export token")
    now = time.time()
    _cleanup_timelapse_export_jobs(now)
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "printer_id": printer_id,
        "task_id": body.task_id,
        "task_name": body.task_name,
        "token": token,
        "status": "generating",
        "message": "Time-lapse video generating…",
        "started_at": now,
        "updated_at": now,
    }
    with _TIMELAPSE_EXPORT_LOCK:
        _TIMELAPSE_EXPORT_JOBS[job_id] = job
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_timelapse_export_job, job_id)
    return {"ok": True, "job_id": job_id, "status": "generating", "message": job["message"], "job": _public_timelapse_export_job(job)}


@app.get("/api/printers/{printer_id}/timelapse/export/{job_id}")
async def api_timelapse_export_status(printer_id: str, job_id: str):
    _raise_if_feature_locked("file_manager_enabled")
    _cleanup_timelapse_export_jobs()
    with _TIMELAPSE_EXPORT_LOCK:
        job = dict(_TIMELAPSE_EXPORT_JOBS.get(job_id) or {})
    if not job or str(job.get("printer_id") or "") != str(printer_id):
        raise HTTPException(404, "Time-lapse export job not found")
    return {"ok": True, "job": _public_timelapse_export_job(job)}


@app.post("/api/printers/{printer_id}/history/delete")
async def api_history_delete(printer_id: str, body: HistoryDeleteRequest):
    _raise_if_feature_locked("file_manager_enabled")
    return await asyncio.to_thread(_send_command, printer_id, HISTORY_DELETE, history_delete_params(body.task_ids), True, 20.0)


@app.post("/api/printers/{printer_id}/control/light")
async def api_control_light(printer_id: str, body: LightRequest):
    _raise_if_control_locked(printer_id)
    result = await asyncio.to_thread(_send_command, printer_id, SET_LIGHT, light_params(body.on), True, 10.0)
    log("info", f"Control light set to {'on' if body.on else 'off'}", "command", printer=printer_id)
    return {"ok": True, "message": f"Light {'on' if body.on else 'off'}", "result": result.get("result")}


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
    phase = status.get("print_phase") if isinstance(status.get("print_phase"), dict) else _print_phase_from_status(status, snap)
    if phase.get("is_preparing"):
        status["print_phase"] = phase
        result = _prep_vision_result(printer_id, status, "manual")
        status["vision_ai"] = result
        status["portal_ai"] = _prep_ai_result(printer_id, status, cfg, "manual")
        return {"ok": True, "skipped": True, "reason": "preparing", "vision": result, "portal_ai": status.get("portal_ai"), "status": status}
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
