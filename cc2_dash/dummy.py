from __future__ import annotations

import io
import math
import time
from typing import Any, Iterable

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - runtime keeps working without Pillow
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]

from .cc2.state import MACHINE_STATUS, SUB_STATUS, seconds_to_hms

DUMMY_MODES = {
    "idle": {"label": "Idle", "status_code": 1, "sub_status_code": 0, "progress": 0},
    "printing": {"label": "Printing", "status_code": 2, "sub_status_code": 2075, "progress": 42},
    "paused": {"label": "Paused", "status_code": 2, "sub_status_code": 2502, "progress": 42},
    "timelapse_generating": {"label": "Time-lapse video generating", "status_code": 12, "sub_status_code": 3020, "progress": 100},
    "error": {"label": "Error", "status_code": 14, "sub_status_code": None, "progress": 13},
    "offline": {"label": "Offline", "status_code": None, "sub_status_code": None, "progress": 0},
}

DUMMY_AI_STATES = {
    "disabled": {"label": "AI disabled", "risk": 0, "level": "low", "state": "disabled"},
    "looks_good": {"label": "Looks good", "risk": 8, "level": "low", "state": "ok"},
    "watch": {"label": "Watch", "risk": 48, "level": "watch", "state": "watch"},
    "warning": {"label": "Something looks fishy", "risk": 68, "level": "medium", "state": "warning"},
    "failure": {"label": "Possible failure detected", "risk": 92, "level": "high", "state": "failure_likely"},
}


def is_dummy_printer(data: Any) -> bool:
    if data is None:
        return False
    value = getattr(data, "type", None) if not isinstance(data, dict) else (data.get("type") or data.get("printer_type"))
    return str(value or "").strip().lower() in {"dummy", "sim", "simulator", "demo"}


def dummy_mode(data: dict[str, Any] | None) -> str:
    value = str((data or {}).get("dummy_mode") or "printing").strip().lower().replace("-", "_")
    return value if value in DUMMY_MODES else "printing"


def dummy_ai_state(data: dict[str, Any] | None) -> str:
    value = str((data or {}).get("dummy_ai_state") or "looks_good").strip().lower().replace("-", "_")
    return value if value in DUMMY_AI_STATES else "looks_good"


def _num(data: dict[str, Any], key: str, default: float) -> float:
    try:
        value = data.get(key)
        if value in (None, ""):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _status_text(status_code: int | None, sub_code: int | None, mode: str) -> tuple[str, str | None]:
    state = MACHINE_STATUS.get(status_code, DUMMY_MODES.get(mode, {}).get("label", "unknown").lower()) if status_code is not None else "offline"
    sub = SUB_STATUS.get(sub_code, f"sub {sub_code}" if sub_code is not None else None)
    return state, sub


def dummy_snapshot(printer_id: str, data: dict[str, Any] | None) -> dict[str, Any]:
    data = data or {}
    mode = dummy_mode(data)
    mode_info = DUMMY_MODES[mode]
    now = time.time()
    online = mode != "offline" and bool(data.get("enabled", True))
    status_code = mode_info.get("status_code")
    sub_status_code = mode_info.get("sub_status_code")
    progress = max(0.0, min(100.0, _num(data, "dummy_progress", mode_info.get("progress", 0) or 0)))
    if mode == "printing" and data.get("dummy_progress_animate", True):
        # Slow harmless drift makes the dashboard feel alive without changing saved config.
        progress = max(progress, min(99.0, progress + ((now / 20.0) % 1.0)))
    elapsed = max(0, int(_num(data, "dummy_elapsed_seconds", 3600)))
    total = max(elapsed + 1, int(_num(data, "dummy_total_seconds", 7200)))
    if mode == "printing":
        remaining = max(0, int(total * (1.0 - progress / 100.0)))
    else:
        remaining = 0
    state, sub_state = _status_text(_int_or_none(status_code), _int_or_none(sub_status_code), mode)
    file_name = str(data.get("dummy_file") or "demo-dragon.gcode")
    exceptions = [1300] if mode == "error" else []
    raw_status = {
        "machine_status": {
            "status": status_code,
            "sub_status": sub_status_code,
            "exception_status": exceptions,
        },
        "print_status": {
            "filename": file_name if mode not in {"idle", "offline"} else "",
            "progress": progress,
            "print_duration": elapsed,
            "total_duration": total,
            "remaining_time_sec": remaining,
            "current_layer": int(max(0, round(progress / 100.0 * _num(data, "dummy_total_layers", 240)))),
            "total_layer": int(_num(data, "dummy_total_layers", 240)),
        },
        "extruder": {"temperature": _num(data, "dummy_hotend_current", 215 if mode == "printing" else 28), "target": _num(data, "dummy_hotend_target", 220 if mode == "printing" else 0)},
        "heater_bed": {"temperature": _num(data, "dummy_bed_current", 59 if mode == "printing" else 27), "target": _num(data, "dummy_bed_target", 60 if mode == "printing" else 0)},
        "ztemperature_sensor": {"temperature": _num(data, "dummy_chamber_current", 34 if mode == "printing" else 25)},
        "fans": {
            "ModelFan": {"speed": 178, "rpm": 3200},
            "AuxiliaryFan": {"speed": 64, "rpm": 1200},
            "BoxFan": {"speed": 90, "rpm": 1800},
        },
        "gcode_move_inf": {"speed_mode": int(_num(data, "dummy_speed_mode", 1)), "speed": _num(data, "dummy_speed_percent", 100)},
        "led": {"status": bool(data.get("dummy_light_on", True))},
    }
    normalized = {
        "state": state,
        "status_code": status_code,
        "sub_state": sub_state,
        "sub_status_code": sub_status_code,
        "exceptions": exceptions,
        "exceptions_raw": exceptions,
        "exception_details": [],
        "exception_summary": "Demo error state" if mode == "error" else "",
        "progress": progress,
        "file": file_name if mode not in {"idle", "offline"} else "",
        "uuid": f"dummy-{printer_id}",
        "layers": {"current": raw_status["print_status"]["current_layer"], "total": raw_status["print_status"]["total_layer"]},
        "time": {"elapsed_sec": elapsed, "total_sec": total, "remaining_sec": remaining, "remaining_human": seconds_to_hms(remaining)},
        "temps": {
            "nozzle": {"actual": raw_status["extruder"]["temperature"], "target": raw_status["extruder"]["target"]},
            "bed": {"actual": raw_status["heater_bed"]["temperature"], "target": raw_status["heater_bed"]["target"]},
            "chamber": {"actual": raw_status["ztemperature_sensor"]["temperature"], "target": None},
        },
        "filament": {"sensor_enabled": True, "detected": mode != "error"},
        "fans": {
            "model": {"speed": 178, "percent": 70, "rpm": 3200},
            "auxiliary": {"speed": 64, "percent": 25, "rpm": 1200},
            "case": {"speed": 90, "percent": 35, "rpm": 1800},
        },
        "position": {"x": 120.0, "y": 120.0, "z": max(0.2, progress / 100.0 * 80.0), "e": None, "speed": 100, "speed_percent": 100, "speed_mode": int(_num(data, "dummy_speed_mode", 1)), "speed_mode_name": "balanced"},
        "toolhead": {"homed_axes": "xyz"},
        "external": {"camera": True, "u_disk": True, "type": "dummy"},
        "led": {"status": bool(data.get("dummy_light_on", True))},
        "attributes": {"hostname": data.get("name") or printer_id, "machine_model": "cc2-dash Dummy Printer", "serial": data.get("serial") or f"DUMMY-{printer_id}", "ip": "dummy", "camera_connected": True},
    }
    return {
        "id": printer_id,
        "name": data.get("name") or "Dummy Printer",
        "host": data.get("host") or "dummy.local",
        "serial": data.get("serial") or f"DUMMY-{printer_id}",
        "type": "dummy",
        "dummy": True,
        "dummy_mode": mode,
        "dummy_ai_state": dummy_ai_state(data),
        "connected": bool(online),
        "registered": bool(online),
        "registration_error": None,
        "last_error": "Dummy printer offline scenario" if not online else "",
        "last_message_age_sec": 0.2 if online else None,
        "last_pong_age_sec": 0.2 if online else None,
        "missed_status_count": 0,
        "allow_commands": bool(data.get("allow_commands", False)),
        "allow_dangerous_commands": bool(data.get("allow_dangerous_commands", False)),
        "normalized": normalized if online else {},
        "attributes": normalized["attributes"] if online else {"hostname": data.get("name") or printer_id, "machine_model": "cc2-dash Dummy Printer", "serial": data.get("serial") or f"DUMMY-{printer_id}"},
        "raw_status": raw_status if online else {},
        "created_epoch": now,
    }


def dummy_ai_result(printer_id: str, status: dict[str, Any], data: dict[str, Any] | None, source: str = "request") -> dict[str, Any]:
    state_key = dummy_ai_state(data)
    state = DUMMY_AI_STATES[state_key]
    enabled = state_key != "disabled"
    now = time.time()
    if not enabled:
        return {
            "enabled": False,
            "risk": 0,
            "level": "low",
            "state": "disabled",
            "message": "Dummy AI scenario is disabled.",
            "reasons": ["Dummy printer AI disabled scenario."],
            "source": source,
            "dummy": True,
            "last_check_epoch": now,
            "last_check": time.strftime("%H:%M:%S"),
        }
    risk = int(state["risk"])
    label = state["label"]
    reasons = [f"Dummy printer scenario: {label}."]
    if status.get("dummy_mode") == "error":
        reasons.append("Dummy printer is in the error scenario.")
    return {
        "enabled": True,
        "risk": risk,
        "level": state["level"],
        "state": state["state"],
        "message": label,
        "summary": label,
        "reasons": reasons,
        "triggered_flags": ["dummy_scenario"],
        "source": source,
        "dummy": True,
        "auto_pause": {"eligible": False, "reason": "Dummy printer never sends real pause/cancel commands."},
        "last_check_epoch": now,
        "last_check": time.strftime("%H:%M:%S"),
    }


def dummy_vision_result(printer_id: str, data: dict[str, Any] | None, source: str = "request") -> dict[str, Any]:
    ai_key = dummy_ai_state(data)
    visual = "ok"
    if ai_key in {"watch", "warning"}:
        visual = "uncertain"
    elif ai_key == "failure":
        visual = "failure_likely"
    return {
        "enabled": ai_key != "disabled",
        "ok": True,
        "visual_state": visual,
        "confidence": 88 if ai_key == "failure" else 75,
        "severity": DUMMY_AI_STATES.get(ai_key, DUMMY_AI_STATES["looks_good"])["risk"],
        "summary": f"Dummy vision scenario: {DUMMY_AI_STATES.get(ai_key, DUMMY_AI_STATES['looks_good'])['label']}.",
        "recommended_action": "inspect" if ai_key in {"warning", "failure"} else "none",
        "failure_types": ["dummy_failure"] if ai_key == "failure" else [],
        "dummy": True,
        "source": source,
        "last_check_epoch": time.time(),
        "last_check": time.strftime("%H:%M:%S"),
    }


def dummy_command_response(printer_id: str, data: dict[str, Any] | None, method: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    params = params or {}
    now = int(time.time())
    # Import locally to avoid import cycles at module import time.
    from .cc2.commands import (
        DELETE_FILE,
        GET_CANVAS_STATUS,
        GET_DISK_INFO,
        GET_FILE_DETAIL,
        GET_FILE_LIST,
        GET_FILE_THUMBNAIL,
        GET_HISTORY_TASK,
        GET_HISTORY_TASK_DETAIL,
        GET_MONO_FILAMENT_INFO,
        GET_TIME_LAPSE_VIDEO_LIST,
        HISTORY_DELETE,
    )

    if method == GET_FILE_LIST:
        media = str(params.get("storage_media") or "local")
        files = [
            {"filename": "demo-dragon.gcode", "file_path": "demo-dragon.gcode", "size": 18400000, "modified_time": now - 3600},
            {"filename": "calibration-cube.gcode", "file_path": "calibration-cube.gcode", "size": 2450000, "modified_time": now - 86400},
            {"filename": "multicolor-prime-tower-test.gcode", "file_path": "multicolor-prime-tower-test.gcode", "size": 32600000, "modified_time": now - 172800},
        ]
        if media == "u-disk":
            files = [{**f, "file_path": "/" + f["filename"]} for f in files]
        return {"error_code": 0, "storage_media": media, "file_list": files, "total": len(files), "dummy": True}
    if method == GET_DISK_INFO:
        return {"error_code": 0, "total": 64 * 1024 * 1024 * 1024, "free": 41 * 1024 * 1024 * 1024, "used": 23 * 1024 * 1024 * 1024, "dummy": True}
    if method == GET_FILE_DETAIL:
        name = params.get("filename") or params.get("file_name") or data.get("dummy_file") or "demo-dragon.gcode"
        return {"error_code": 0, "filename": name, "size": 18400000, "total_layer": 240, "estimated_time": 7200, "dummy": True}
    if method == GET_FILE_THUMBNAIL:
        return {"error_code": 0, "dummy": True, "message": "Dummy files do not include embedded G-code thumbnails yet."}
    if method == GET_HISTORY_TASK:
        return {"error_code": 0, "history_list": [
            {"task_id": "dummy-history-1", "task_name": "demo-dragon.gcode", "begin_time": now - 10800, "end_time": now - 7200, "task_status": "completed", "time_lapse_video_status": 1, "file_size": 18400000},
            {"task_id": "dummy-history-2", "task_name": "prime-tower-test.gcode", "begin_time": now - 24000, "end_time": now - 21000, "task_status": "cancelled", "time_lapse_video_status": 0, "file_size": 32600000},
        ], "total": 2, "dummy": True}
    if method == GET_HISTORY_TASK_DETAIL:
        return {"error_code": 0, "task_id": params.get("task_id") or params.get("id") or "dummy-history-1", "dummy": True}
    if method == GET_TIME_LAPSE_VIDEO_LIST:
        return {"error_code": 0, "video_list": [
            {"file_name": "demo-dragon.mp4", "name": "demo-dragon.mp4", "file_size": 52428800, "time_lapse_video_status": 2, "status": 2, "url": "/dummy/demo-dragon.mp4"},
            {"file_name": "needs-export.mp4", "name": "needs-export.mp4", "file_size": 0, "time_lapse_video_status": 1, "status": 1},
        ], "total": 2, "dummy": True}
    if method in {DELETE_FILE, HISTORY_DELETE}:
        return {"error_code": 0, "message": "Dummy delete accepted for UI testing only; no real file was removed.", "dummy": True}
    if method in {GET_CANVAS_STATUS, GET_MONO_FILAMENT_INFO}:
        return {"error_code": 0, "dummy": True, "trays": []}
    return {"error_code": 0, "message": f"Dummy method {method} accepted for UI testing only; no real command was sent.", "dummy": True}


def dummy_camera_frame(printer_id: str, data: dict[str, Any] | None, *, width: int = 960, height: int = 540) -> bytes:
    data = data or {}
    mode = dummy_mode(data)
    ai = dummy_ai_state(data)
    name = str(data.get("name") or "Dummy Printer")
    if Image is None or ImageDraw is None:
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="#10151f"/><text x="36" y="70" fill="#ffffff" font-size="32" font-family="monospace">cc2-dash Dummy Printer</text><text x="36" y="120" fill="#88d4ff" font-size="24" font-family="monospace">{name} · {mode}</text><text x="36" y="160" fill="#f6c177" font-size="20" font-family="monospace">AI: {ai}</text></svg>'''
        return svg.encode("utf-8")
    img = Image.new("RGB", (width, height), (12, 18, 30))
    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        font_med = ImageFont.truetype("DejaVuSans.ttf", 22)
        font_small = ImageFont.truetype("DejaVuSansMono.ttf", 18)
    except Exception:
        font_big = font_med = font_small = None
    # Fake camera frame: bed plate, part, prime tower.
    draw.rectangle((0, 0, width, height), fill=(10, 15, 26))
    draw.rounded_rectangle((42, 52, width - 42, height - 42), radius=26, outline=(76, 115, 150), width=3, fill=(22, 30, 42))
    bed = (125, 130, width - 125, height - 72)
    draw.rounded_rectangle(bed, radius=16, fill=(54, 59, 70), outline=(134, 150, 166), width=2)
    # grid lines
    for i in range(1, 6):
        x = bed[0] + (bed[2] - bed[0]) * i / 6
        draw.line((x, bed[1], x, bed[3]), fill=(74, 80, 92), width=1)
    for i in range(1, 4):
        y = bed[1] + (bed[3] - bed[1]) * i / 4
        draw.line((bed[0], y, bed[2], y), fill=(74, 80, 92), width=1)
    # object
    obj_color = (69, 174, 226) if ai != "failure" else (232, 98, 76)
    cx = (bed[0] + bed[2]) / 2
    cy = (bed[1] + bed[3]) / 2 + 20
    draw.ellipse((cx - 78, cy - 58, cx + 78, cy + 58), fill=obj_color, outline=(220, 240, 255), width=2)
    draw.rectangle((cx - 34, cy - 100, cx + 34, cy + 35), fill=obj_color, outline=(220, 240, 255), width=2)
    # prime tower / failure visual
    tower_x = bed[2] - 120
    if ai == "failure":
        draw.polygon([(tower_x - 55, bed[3] - 40), (tower_x + 60, bed[3] - 66), (tower_x + 50, bed[3] - 92), (tower_x - 60, bed[3] - 66)], fill=(238, 102, 89), outline=(255, 228, 220))
        draw.line((tower_x - 25, bed[3] - 100, tower_x + 45, bed[3] - 54), fill=(255, 210, 90), width=4)
    else:
        draw.rounded_rectangle((tower_x - 30, bed[3] - 138, tower_x + 30, bed[3] - 40), radius=6, fill=(215, 151, 62), outline=(255, 224, 145), width=2)
    # toolhead marker
    t = time.time()
    nozzle_x = int(cx + math.sin(t / 2.5) * 170)
    nozzle_y = int(bed[1] + 52 + math.cos(t / 3.0) * 20)
    draw.polygon([(nozzle_x - 32, nozzle_y - 18), (nozzle_x + 32, nozzle_y - 18), (nozzle_x + 20, nozzle_y + 18), (nozzle_x - 20, nozzle_y + 18)], fill=(34, 38, 47), outline=(185, 199, 214))
    draw.rectangle((nozzle_x - 6, nozzle_y + 18, nozzle_x + 6, nozzle_y + 36), fill=(220, 226, 235))
    # labels
    draw.text((62, 76), "cc2-dash Dummy Printer", fill=(235, 243, 255), font=font_big)
    draw.text((64, 118), f"{name} · {DUMMY_MODES[mode]['label']} · AI: {DUMMY_AI_STATES[ai]['label']}", fill=(152, 217, 255), font=font_med)
    draw.text((64, height - 34), "Demo/simulator frame — no real camera or printer commands", fill=(180, 188, 202), font=font_small)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=86)
    return out.getvalue()


def mjpeg_frames(printer_id: str, data: dict[str, Any] | None, delay_seconds: float = 1.0) -> Iterable[bytes]:
    while True:
        frame = dummy_camera_frame(printer_id, data)
        yield b"--cc2dashframe\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + frame + b"\r\n"
        time.sleep(max(0.25, float(delay_seconds or 1.0)))
