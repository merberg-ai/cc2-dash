from __future__ import annotations

from copy import deepcopy


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
    return {
        "state": data.get("status_name") or data.get("status") or "unknown",
        "sub_state": data.get("sub_status_name") or data.get("sub_status"),
        "progress": data.get("progress", 0),
        "file": data.get("filename") or data.get("file"),
        "temps": {
            "nozzle": {"actual": temp.get("nozzle"), "target": temp.get("target_nozzle")},
            "bed": {"actual": temp.get("bed"), "target": temp.get("target_bed")},
            "chamber": {"actual": temp.get("chamber")},
        },
    }
