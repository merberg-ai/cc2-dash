from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional


MACHINE_STATUS = {
    0: "initializing",
    1: "idle",
    2: "printing",
    3: "filament operating",
    4: "filament operating",
    5: "auto leveling",
    6: "pid calibrating",
    7: "resonance testing",
    8: "self checking",
    9: "updating",
    10: "homing",
    11: "file transferring",
    12: "time-lapse video generating",
    13: "extruder operating",
    14: "emergency stop",
    15: "power loss recovery",
    16: "completed",
    18: "time-lapse video generating",
}

SUB_STATUS = {
    0: "idle",
    1041: "idle in print",
    1045: "extruder preheating",
    1096: "extruder preheating",
    1405: "bed preheating",
    1906: "bed preheating",
    2075: "printing",
    2077: "completed",
    2401: "resuming",
    2402: "resume complete",
    2501: "pausing",
    2502: "paused",
    2505: "paused",
    2503: "stopping",
    2504: "stopped",
    2801: "homing",
    2802: "homing complete",
    2901: "auto leveling",
    2902: "auto leveling complete",
    3020: "time-lapse video generating",
}

SPEED_MODES = {
    0: "silent",
    1: "balanced",
    2: "sport",
    3: "ludicrous",
}


# Printer exception code meanings gathered from the bundled stock ELEGOO portal
# translation table, then expanded with public Centauri Carbon 2 Combo wiki pages.
# Keep these short: they are shown in dashboard AI reasons and status payloads.
PRINTER_EXCEPTION_CODES: dict[int, dict[str, str]] = {
    101: {"label": "Bed heat failed", "message": "Failed to heat the bed"},
    102: {"label": "Bed temperature sensor disconnected", "message": "Heated bed temperature sensor disconnected"},
    103: {"label": "Nozzle heat failed", "message": "Failed to heat the nozzle"},
    104: {"label": "Nozzle temperature sensor disconnected", "message": "Nozzle temperature sensor disconnected"},
    105: {"label": "Nozzle temperature sensor shorted", "message": "Nozzle temperature sensor shorted"},
    106: {"label": "Bed temperature sensor shorted", "message": "Heated bed temperature sensor shorted"},
    107: {"label": "Toolhead overheating protection", "message": "Toolhead overheating protection"},
    108: {"label": "Bed overheating protection", "message": "Heated bed overheating protection"},
    109: {"label": "Filament runout", "message": "Filament run out"},
    205: {"label": "Chamber temperature sensor disconnected", "message": "Chamber temperature sensor disconnected"},
    206: {"label": "Chamber temperature sensor shorted", "message": "Chamber temperature sensor shorted"},
    304: {"label": "Z-axis homing failed", "message": "Failed to home Z-axis"},
    401: {"label": "Accelerometer chip error", "message": "Accelerometer chip error"},
    605: {"label": "Pressure sensor data error", "message": "Pressure sensor data error"},
    701: {"label": "Mainboard fan error", "message": "Mainboard fan error"},
    702: {"label": "Heatbreak fan error", "message": "Heatbreak fan error"},
    703: {"label": "Model cooling fan error", "message": "Model cooling fan error"},
    704: {"label": "Leveling failed", "message": "Leveling failed"},
    705: {"label": "Auxiliary fan error", "message": "Auxiliary fan error"},
    706: {"label": "Case fan error", "message": "Case/chamber cooling fan error"},
    707: {"label": "Toolhead front cover detached", "message": "Toolhead front cover detached"},
    801: {"label": "Mainboard-extruder communication error", "message": "Mainboard-extruder communication error"},
    802: {"label": "Leveling sensor controller communication error", "message": "Control board communication error of the leveling sensor"},
    803: {"label": "Critical system error", "message": "A critical system error occurred"},
    901: {"label": "Chamber temperature too high", "message": "Chamber temperature is too high"},
    902: {"label": "Chamber overheating protection", "message": "Chamber overheating protection"},
    903: {"label": "Mainboard driver overheating protection", "message": "Motherboard drive unit overheating protection"},
    904: {"label": "USB storage space low", "message": "Insufficient USB drive storage space"},
    905: {"label": "USB drive read error", "message": "USB drive read error"},
    906: {"label": "Version update failed", "message": "Version update failed"},
    1101: {"label": "Exhaust vent open failed", "message": "Failed to open exhaust vent"},
    1102: {"label": "Exhaust vent close failed", "message": "Failed to close exhaust vent"},
    1210: {"label": "CANVAS communication error", "message": "CANVAS communication error"},
    1211: {"label": "Filament runout", "message": "Filament ran out or was not detected during printing"},
    1220: {"label": "Extruder error", "message": "Abnormal filament detected in the toolhead"},
    1231: {"label": "Filament cut failed", "message": "Filament cut failed"},
    1232: {"label": "Cutter handle not released", "message": "Cutter handle not released"},
    1241: {"label": "Loading error", "message": "Loading error"},
    1242: {"label": "Unload failure at toolhead", "message": "Failed to unload filament at the toolhead"},
    1243: {"label": "Toolhead extrusion failed", "message": "Filament extrusion failed at the toolhead"},
    1244: {"label": "Toolhead extrusion failed", "message": "Filament extrusion failed at the toolhead"},
    1251: {"label": "Toolhead extrusion failed", "message": "Filament extrusion failed at the toolhead"},
    1252: {"label": "Extruder unload failure (unload timeout)", "message": "Failed to unload filament at the toolhead"},
    1261: {"label": "Toolhead front cover detached", "message": "Toolhead front cover detached"},
    1262: {"label": "Cutter handle not released", "message": "Cutter handle not released"},
    1263: {"label": "Filament feeding abnormality", "message": "Filament feeding abnormality / tangling abnormality triggered"},
    1264: {"label": "Toolhead extrusion failed", "message": "Filament extrusion failed at the toolhead"},
    1265: {"label": "Toolhead extrusion failed", "message": "Filament extrusion failed at the toolhead"},
    1266: {"label": "Filament detection error at toolhead", "message": "Filament detection board abnormal detection during printing"},
    1267: {"label": "No filament detected at toolhead", "message": "No filament detected at the toolhead; filament may be broken inside the PTFE tube"},
    1300: {"label": "Print file unavailable", "message": "Print file unavailable"},
    2003: {"label": "Cloud/network initialization error", "message": "Printer network/cloud initialization failed after connecting to WiFi"},
}

PRINTER_EXCEPTION_HINTS: dict[int, str] = {
    1211: "Check whether filament is loaded into CANVAS and the filament detection board can see it.",
    1220: "Check for filament stuck in the toolhead, PTFE tube, or sensor path.",
    1231: "Check the cutter, cutter handle, blade movement, and toolhead cover seating.",
    1241: "Check CANVAS feed path, PTFE tube, 4-in-1 hub, and toolhead entry path.",
    1252: "Check for broken or stuck filament in the PTFE tube, 4-in-1 hub, and toolhead; also verify the filament detection board.",
    1263: "Check for tangled filament, oversized filament diameter, CANVAS feed obstruction, or gearbox debris.",
    1266: "Reseat the filament detection board connectors; replace the board if the error persists.",
    1267: "Check for broken filament in the PTFE tube/toolhead and verify the filament detection board connectors.",
    2003: "Restart printer/router, verify region/timezone/network, toggle LAN-only mode, then update slicer/firmware if needed.",
}


def _coerce_exception_code(value: Any) -> int | str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        code = int(float(value))
        return None if code == 0 else code
    except Exception:
        text = str(value).strip()
        return text or None


def _flatten_exception_values(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        preferred = [
            "code", "Code", "error_code", "errorCode", "ErrorCode",
            "exception_status", "exceptionStatus", "status", "value",
        ]
        found: list[Any] = [value[k] for k in preferred if k in value]
        return found or list(value.values())
    if isinstance(value, (list, tuple, set)):
        out: list[Any] = []
        for item in value:
            out.extend(_flatten_exception_values(item))
        return out
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        # Firmware/debug strings commonly arrive as "[1252]", "1252,1267", or
        # "1252 1267". Split those without mangling unknown textual messages.
        cleaned = text.strip("[](){}")
        if any(sep in cleaned for sep in (",", ";", " ")):
            parts = [p.strip() for chunk in cleaned.split(";") for p in chunk.replace(",", " ").split()]
            if parts and all(p.replace(".", "", 1).lstrip("+-").isdigit() for p in parts):
                return parts
        return [cleaned]
    return [value]


def exception_codes(value: Any) -> list[int | str]:
    """Return stable, de-duplicated printer exception codes from firmware payloads."""
    out: list[int | str] = []
    seen: set[str] = set()
    for raw in _flatten_exception_values(value):
        code = _coerce_exception_code(raw)
        if code is None:
            continue
        key = str(code)
        if key not in seen:
            out.append(code)
            seen.add(key)
    return out


def describe_exception_codes(value: Any) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for code in exception_codes(value):
        info = PRINTER_EXCEPTION_CODES.get(code) if isinstance(code, int) else None
        label = (info or {}).get("label") or "Unknown exception"
        message = (info or {}).get("message") or "No stock ELEGOO meaning is known for this exception code yet"
        text = f"{code} — {label}"
        if message and message.lower() != label.lower():
            text = f"{text}: {message}"
        hint = PRINTER_EXCEPTION_HINTS.get(code) if isinstance(code, int) else None
        details.append({
            "code": code,
            "known": bool(info),
            "label": label,
            "message": message,
            "hint": hint,
            "text": text,
        })
    return details


def format_exception_codes(value: Any) -> str:
    details = describe_exception_codes(value)
    return "; ".join(str(d.get("text") or d.get("code")) for d in details)


def deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge CC2 delta status payloads into the full status cache."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)  # type: ignore[index]
        else:
            dst[key] = deepcopy(value)
    return dst




def boolish(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "enable", "filament", "detected", "present", "loaded"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable", "none", "empty", "no_filament", "nofilament", "/"}:
        return False
    return None


def get_path(data: Dict[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        cur: Any = data
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return default


def fan_to_percent(speed: Any, *, assume_pwm: bool = True) -> Optional[int]:
    """Normalize stock fan telemetry to a user-facing percent.

    The CC2 MQTT ``fans.*.speed`` values used by the bundled stock portal are
    PWM-ish 0-255 values and the portal displays them as ``speed * 100 / 255``.
    Older SDCP/websocket ``CurrentFanSpeed`` values are percent-shaped in public
    docs, so those are treated as 0-100 unless they exceed 100.
    """
    if speed in (None, ""):
        return None
    try:
        value = float(speed)
    except Exception:
        return None
    if assume_pwm:
        # Be tolerant of Klipper-style normalized fan speeds if they ever leak
        # through a printer/firmware variant.  The CC2 stock portal path uses
        # 0-255, but 0.0-1.0 should still display sensibly.
        if 0.0 <= value <= 1.0 and not float(value).is_integer():
            pct = value * 100.0
        else:
            pct = value / 255.0 * 100.0
    else:
        pct = value if value <= 100.0 else value / 255.0 * 100.0
    return int(max(0, min(100, round(pct))))


def _coord_value(full_status: Dict[str, Any], index: int, fallback: Any = None) -> Any:
    coord = get_path(full_status, "CurrenCoord", "CurrentCoord", "current_coord", "currentCoord")
    if isinstance(coord, str):
        parts = [p.strip() for p in coord.split(",")]
        if len(parts) > index:
            try:
                return round(float(parts[index]), 3)
            except Exception:
                return parts[index]
    if isinstance(coord, (list, tuple)) and len(coord) > index:
        return coord[index]
    return fallback


def seconds_to_hms(seconds: Any) -> Optional[str]:
    try:
        total = int(float(seconds))
    except Exception:
        return None
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def normalize_status(full_status: Dict[str, Any], attributes: Dict[str, Any] | None = None) -> Dict[str, Any]:
    attributes = attributes or {}
    machine_status_code = get_path(full_status, "machine_status.status")
    sub_status_code = get_path(full_status, "machine_status.sub_status")
    progress = get_path(full_status, "print_status.progress", "machine_status.progress", default=0)

    fans = get_path(full_status, "fans", default=None)
    fans_are_pwm = True
    if not isinstance(fans, dict):
        fans = get_path(full_status, "CurrentFanSpeed", "currentFanSpeed", default={}) or {}
        fans_are_pwm = False
    move = get_path(full_status, "gcode_move_inf", "gcode_move", default={}) or {}
    extruder_e = move.get("e", move.get("extruder")) if isinstance(move, dict) else None
    speed_mode = None
    if isinstance(move, dict):
        speed_mode = move.get("speed_mode", move.get("SpeedMode"))
    if speed_mode is None:
        speed_mode = get_path(
            full_status,
            "print_status.speed_mode",
            "print_status.print_speed_mode",
            "print_status.PrintSpeedMode",
            "print_info.speed_mode",
            "print_info.SpeedMode",
            "PrintInfo.speed_mode",
            "PrintInfo.SpeedMode",
            "gcode_move.speed_mode",
            "gcode_move.SpeedMode",
            "gcode_move_inf.speed_mode",
            "gcode_move_inf.SpeedMode",
        )
    try:
        speed_mode = int(float(speed_mode)) if speed_mode is not None and speed_mode != "" else None
    except Exception:
        pass

    raw_exceptions = get_path(full_status, "machine_status.exception_status", default=[])
    exception_details = describe_exception_codes(raw_exceptions)

    normalized = {
        "state": MACHINE_STATUS.get(machine_status_code, f"unknown ({machine_status_code})" if machine_status_code is not None else "unknown"),
        "status_code": machine_status_code,
        "sub_state": SUB_STATUS.get(sub_status_code, f"sub {sub_status_code}" if sub_status_code is not None else None),
        "sub_status_code": sub_status_code,
        "exceptions": [detail.get("code") for detail in exception_details],
        "exceptions_raw": raw_exceptions,
        "exception_details": exception_details,
        "exception_summary": format_exception_codes(raw_exceptions),
        "progress": progress,
        "file": get_path(full_status, "print_status.filename"),
        "uuid": get_path(full_status, "print_status.uuid"),
        "layers": {
            "current": get_path(full_status, "print_status.current_layer"),
            "total": get_path(full_status, "print_status.total_layer"),
        },
        "time": {
            "elapsed_sec": get_path(full_status, "print_status.print_duration"),
            "total_sec": get_path(full_status, "print_status.total_duration"),
            "remaining_sec": get_path(full_status, "print_status.remaining_time_sec"),
            "remaining_human": seconds_to_hms(get_path(full_status, "print_status.remaining_time_sec")),
        },
        "temps": {
            "nozzle": {
                "actual": get_path(full_status, "extruder.temperature"),
                "target": get_path(full_status, "extruder.target"),
            },
            "bed": {
                "actual": get_path(full_status, "heater_bed.temperature"),
                "target": get_path(full_status, "heater_bed.target"),
            },
            "chamber": {
                "actual": get_path(full_status, "ztemperature_sensor.temperature", "chamber.temperature"),
                "target": get_path(full_status, "chamber.target"),
            },
        },
        "filament": {
            "sensor_enabled": boolish(get_path(
                full_status,
                "extruder.filament_detect_enable",
                "extruder.filament_detect_enabled",
                "deviceStatus.extruder.filament_detect_enable",
                "deviceStatus.extruder.filament_detect_enabled",
                "device_status.extruder.filament_detect_enable",
                "device_status.extruder.filament_detect_enabled",
                "filament_sensor.enabled",
                "filamentSensor.enabled",
            )),
            "detected": boolish(get_path(
                full_status,
                "extruder.filament_detected",
                "extruder.filament_detect",
                "deviceStatus.extruder.filament_detected",
                "deviceStatus.extruder.filament_detect",
                "device_status.extruder.filament_detected",
                "device_status.extruder.filament_detect",
                "filament_sensor.detected",
                "filament_sensor.status",
                "filamentSensor.detected",
                "filamentSensor.status",
            )),
        },
        "fans": {},
        "position": {
            "x": _coord_value(full_status, 0, move.get("x") if isinstance(move, dict) else None),
            "y": _coord_value(full_status, 1, move.get("y") if isinstance(move, dict) else None),
            "z": _coord_value(full_status, 2, move.get("z") if isinstance(move, dict) else None),
            "e": extruder_e,
            "speed": (move.get("speed", move.get("Speed")) if isinstance(move, dict) else None) or get_path(full_status, "print_status.speed", "print_status.feedrate", "gcode_move.speed", "PrintInfo.PrintSpeedPct", "print_info.print_speed_pct", "print_status.PrintSpeedPct"),
            "speed_percent": get_path(full_status, "PrintInfo.PrintSpeedPct", "print_info.print_speed_pct", "print_status.PrintSpeedPct", "print_status.speed_percent"),
            "speed_mode": speed_mode,
            "speed_mode_name": SPEED_MODES.get(speed_mode, str(speed_mode) if speed_mode is not None else None),
        },
        "toolhead": {
            "homed_axes": get_path(full_status, "toolhead.homed_axes", "tool_head.homed_axes"),
        },
        "external": {
            "camera": get_path(full_status, "external_device.camera"),
            "u_disk": get_path(full_status, "external_device.u_disk"),
            "type": get_path(full_status, "external_device.type"),
        },
        "led": {
            "status": get_path(full_status, "led.status"),
        },
        "attributes": {
            "hostname": attributes.get("hostname") or attributes.get("host_name"),
            "machine_model": attributes.get("machine_model"),
            "serial": attributes.get("sn") or attributes.get("serial"),
            "ip": attributes.get("ip"),
            "mac": attributes.get("mac"),
            "software_version": attributes.get("software_version"),
            "camera_connected": attributes.get("camera_connected"),
            "video_connections": attributes.get("video_connections"),
            "max_video_connections": attributes.get("max_video_connections"),
        },
    }

    if isinstance(fans, dict):
        stock_fan_names = {
            "ModelFan": "model",
            "modelFan": "model",
            "fan": "model",
            "AuxiliaryFan": "auxiliary",
            "auxiliaryFan": "auxiliary",
            "aux_fan": "auxiliary",
            "BoxFan": "case",
            "boxFan": "case",
            "box_fan": "case",
            "ChamberFan": "case",
            "chamber_fan": "case",
        }
        for name, fan in fans.items():
            key = stock_fan_names.get(str(name), str(name))
            if isinstance(fan, dict):
                speed = fan.get("speed", fan.get("Speed"))
                normalized["fans"][key] = {
                    "speed": speed,
                    "percent": fan_to_percent(speed, assume_pwm=fans_are_pwm),
                    "rpm": fan.get("rpm", fan.get("RPM")),
                }
            elif isinstance(fan, (int, float, str)) and str(fan).strip() != "":
                normalized["fans"][key] = {
                    "speed": fan,
                    "percent": fan_to_percent(fan, assume_pwm=fans_are_pwm),
                    "rpm": None,
                }

    return normalized
