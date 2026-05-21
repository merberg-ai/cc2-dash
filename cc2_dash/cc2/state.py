from __future__ import annotations

from copy import deepcopy

STATUS_MAP = {
    0: "initializing", 1: "idle", 2: "printing", 3: "filament operating", 4: "filament operating",
    5: "auto leveling", 6: "pid calibrating", 7: "resonance testing", 8: "self checking", 9: "updating",
    10: "homing", 11: "file transferring", 12: "video composing", 13: "extruder operating", 14: "emergency stop",
    15: "power loss recovery",
}


def deep_merge(dst: dict, src: dict) -> dict:
    out = deepcopy(dst)
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def normalize_status(full_status: dict) -> dict:
    data = full_status.get("print_info") or full_status
    temp = full_status.get("temp") or {}
    state_num = data.get("status")
    return {
        "state": data.get("status_name") or STATUS_MAP.get(state_num, state_num or "unknown"),
        "sub_state": data.get("sub_status_name") or data.get("sub_status"),
        "progress": data.get("progress", 0),
        "file": data.get("filename") or data.get("file"),
        "layers": {"current": data.get("layer"), "total": data.get("total_layer")},
        "time": {"remaining": data.get("remain_time")},
        "temps": {
            "nozzle": {"actual": temp.get("nozzle"), "target": temp.get("target_nozzle")},
            "bed": {"actual": temp.get("bed"), "target": temp.get("target_bed")},
            "chamber": {"actual": temp.get("chamber")},
        },
        "fans": full_status.get("fan") or {},
        "position": full_status.get("position") or {},
        "attributes": full_status.get("attributes") or {},
    }
