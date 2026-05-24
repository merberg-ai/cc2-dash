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
    12: "video composing",
    13: "extruder operating",
    14: "emergency stop",
    15: "power loss recovery",
}

SUB_STATUS = {
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
}

SPEED_MODES = {
    0: "silent",
    1: "balanced",
    2: "sport",
    3: "ludicrous",
}


def deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge CC2 delta status payloads into the full status cache."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)  # type: ignore[index]
        else:
            dst[key] = deepcopy(value)
    return dst


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


def fan_to_percent(speed: Any) -> Optional[int]:
    try:
        return round(float(speed) / 255.0 * 100.0)
    except Exception:
        return None


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

    fans = get_path(full_status, "fans", default={}) or {}
    move = get_path(full_status, "gcode_move_inf", "gcode_move", default={}) or {}
    extruder_e = move.get("e", move.get("extruder")) if isinstance(move, dict) else None
    speed_mode = move.get("speed_mode") if isinstance(move, dict) else None

    normalized = {
        "state": MACHINE_STATUS.get(machine_status_code, f"unknown ({machine_status_code})" if machine_status_code is not None else "unknown"),
        "status_code": machine_status_code,
        "sub_state": SUB_STATUS.get(sub_status_code, f"sub {sub_status_code}" if sub_status_code is not None else None),
        "sub_status_code": sub_status_code,
        "exceptions": get_path(full_status, "machine_status.exception_status", default=[]),
        "progress": progress,
        "file": get_path(full_status, "print_status.filename"),
        "uuid": get_path(full_status, "print_status.uuid"),
        "layers": {
            "current": get_path(
                full_status,
                "print_status.current_layer",
                "print_status.info.current_layer",
                "print_stats.info.current_layer",
                "print_stats.current_layer",
                "print_status.current_layers",
            ),
            "total": get_path(
                full_status,
                "print_status.total_layer",
                "print_status.info.total_layer",
                "print_stats.info.total_layer",
                "print_stats.total_layer",
                "print_status.total_layers",
            ),
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
            "sensor_enabled": get_path(full_status, "extruder.filament_detect_enable"),
            "detected": get_path(full_status, "extruder.filament_detected"),
        },
        "fans": {},
        "position": {
            "x": move.get("x") if isinstance(move, dict) else None,
            "y": move.get("y") if isinstance(move, dict) else None,
            "z": move.get("z") if isinstance(move, dict) else None,
            "e": extruder_e,
            "speed": move.get("speed") if isinstance(move, dict) else None,
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
        for name, fan in fans.items():
            if isinstance(fan, dict):
                normalized["fans"][name] = {
                    "speed": fan.get("speed"),
                    "percent": fan_to_percent(fan.get("speed")),
                    "rpm": fan.get("rpm"),
                }

    return normalized
