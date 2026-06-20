from __future__ import annotations

from typing import Any


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
PRINT_PREP_MACHINE_STATUS_CODES = {0, 5, 8, 10}
PRINT_PREP_SUB_STATUS_CODES = {1041, 1045, 1096, 1405, 1906, 2801, 2802, 2901, 2902}
PRINT_PREP_TEXT_TERMS = (
    "preheating",
    "extruder heating",
    "extruder preheating",
    "bed heating",
    "bed preheating",
    "heating bed",
    "homing",
    "auto leveling",
    "leveling",
    "self checking",
    "initializing",
    "warming",
    "warmup",
    "warm up",
)


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


def _has_real_file(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"-", "none", "None", "null"})


def normalized_from_snapshot(snap: dict[str, Any] | None) -> dict[str, Any]:
    return ((snap or {}).get("normalized") or {}) if isinstance(snap, dict) else {}


def print_phase_from_status(status: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify normal start-of-job prep states that should not be scored as failures."""
    status = status or {}
    existing_phase = status.get("print_phase") if isinstance(status.get("print_phase"), dict) else None
    if existing_phase and "is_preparing" in existing_phase:
        return dict(existing_phase)
    n = normalized_from_snapshot(snap)
    machine_code = _coerce_int(status.get("status_code", n.get("status_code")))
    sub_code = _coerce_int(status.get("sub_status_code", n.get("sub_status_code")))
    state_text = " ".join(
        str(x or "")
        for x in (
            status.get("state"),
            status.get("status_text"),
            n.get("state"),
            n.get("sub_state"),
        )
    ).strip().lower()

    is_preparing = bool(
        machine_code in PRINT_PREP_MACHINE_STATUS_CODES
        or sub_code in PRINT_PREP_SUB_STATUS_CODES
        or any(term in state_text for term in PRINT_PREP_TEXT_TERMS)
    )
    if "bed preheating" in state_text or sub_code in {1405, 1906}:
        kind = "bed_preheating"
        label = "Bed Preheating"
    elif ("extruder" in state_text and ("preheat" in state_text or "heating" in state_text)) or sub_code in {1045, 1096}:
        kind = "extruder_preheating"
        label = "Extruder Preheating"
    elif "homing" in state_text or machine_code == 10 or sub_code in {2801, 2802}:
        kind = "homing"
        label = "Homing"
    elif "level" in state_text or machine_code == 5 or sub_code in {2901, 2902}:
        kind = "auto_leveling"
        label = "Auto Leveling"
    elif "self" in state_text or machine_code == 8:
        kind = "self_checking"
        label = "Self Checking"
    elif "initial" in state_text or machine_code == 0:
        kind = "initializing"
        label = "Initializing"
    else:
        kind = "preparing"
        label = str(status.get("status_text") or n.get("sub_state") or status.get("state") or n.get("state") or "Preparing")

    return {
        "is_preparing": is_preparing,
        "kind": kind,
        "label": label,
        "status_code": machine_code,
        "sub_status_code": sub_code,
    }


def status_looks_active_print(status: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> bool:
    """Best-effort active print detector shared by dashboard, AI, vision, and action gates."""
    status = status or {}
    n = normalized_from_snapshot(snap)
    machine_code = _coerce_int(status.get("status_code", n.get("status_code")))
    sub_code = _coerce_int(status.get("sub_status_code", n.get("sub_status_code")))

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


def status_is_safe_to_pause(status: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Return whether an automated pause command is even allowed to be considered."""
    status = status or {}
    active_print = bool(status.get("active_print")) or status_looks_active_print(status, snap)
    if not active_print:
        return False, "printer is not reporting an active print"
    phase = status.get("print_phase") if isinstance(status.get("print_phase"), dict) else print_phase_from_status(status, snap)
    if phase.get("is_preparing"):
        return False, "printer is still preparing the job"
    state_text = f"{status.get('state') or ''} {status.get('status_text') or ''}".lower()
    if any(word in state_text for word in ("idle", "standby", "complete", "finished", "stopped", "stopping")) and "print" not in state_text:
        return False, "printer is not in a running print state"
    if any(word in state_text for word in ("paused", "pausing")):
        return False, "printer is already paused"
    if not status.get("reachable") or not status.get("connected"):
        return False, "printer is not connected enough to send a pause command"
    return True, "eligible"


def classify_status(status: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> dict[str, Any]:
    phase = print_phase_from_status(status, snap)
    active = status_looks_active_print(status, snap)
    safe, reason = status_is_safe_to_pause(status, snap)
    return {
        "active_print": active,
        "print_phase": phase,
        "safe_to_pause": safe,
        "pause_safety_reason": reason,
    }
