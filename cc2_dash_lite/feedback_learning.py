from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .logger import log

SUPPRESSIONS_PATH = DATA_DIR / "ai_feedback_suppressions.json"
FALSE_POSITIVE_OUTCOMES = {"false_positive"}
CONCERNING_STATES = {"possible_failure", "failure_likely", "camera_bad"}
CONCERNING_HEURISTIC_WARNINGS = {
    "dark_frame",
    "light_drop_detected",
    "high_fine_edge_density",
    "fine_edge_density_jump",
    "heuristics_error",
    "telemetry_model_mismatch",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(_as_float(value, float(default))))
    except Exception:
        return default


def _feedback_kind(label: str) -> str:
    label = str(label or "").strip().lower()
    if label in {"looks_good", "good", "ok"}:
        return "positive"
    if label in {"looks_bad", "bad", "failure", "problem"}:
        return "failure"
    if label in {"false_alarm", "false-positive", "false_positive"}:
        return "false_alarm"
    return "unknown"


def _heuristics_from_vision(vision: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(vision, dict):
        return {}
    heur = vision.get("heuristics")
    return heur if isinstance(heur, dict) else {}


def _warning_list(vision: dict[str, Any] | None = None, heuristics: dict[str, Any] | None = None) -> list[str]:
    heuristics = heuristics if isinstance(heuristics, dict) else _heuristics_from_vision(vision)
    warnings = heuristics.get("warnings") if isinstance(heuristics, dict) else []
    if not isinstance(warnings, list):
        warnings = []
    return sorted({str(w) for w in warnings if str(w).strip()})


def _failure_types(vision: dict[str, Any] | None) -> list[str]:
    if not isinstance(vision, dict):
        return []
    raw = vision.get("failure_types") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return sorted({str(x).strip() for x in raw if str(x).strip()})


def _vision_is_warning(vision: dict[str, Any] | None) -> bool:
    if not isinstance(vision, dict):
        return False
    visual_state = str(vision.get("visual_state") or vision.get("state") or "").strip().lower()
    heur = _heuristics_from_vision(vision)
    warnings = set(_warning_list(vision, heur))
    confidence = _as_float(vision.get("confidence"), 0.0)
    severity = _as_float(vision.get("severity"), 0.0)
    if bool(vision.get("bad_now")) or bool(vision.get("bad_confirmed")):
        return True
    if visual_state in {"possible_failure", "failure_likely"}:
        return True
    if visual_state == "camera_bad" and severity >= 25:
        return True
    if bool(heur.get("possible_stringing")) or bool(heur.get("camera_bad")):
        return True
    if warnings.intersection(CONCERNING_HEURISTIC_WARNINGS):
        return True
    return visual_state in CONCERNING_STATES and (confidence >= 50 or severity >= 30)


def _portal_is_warning(portal: dict[str, Any] | None) -> bool:
    if not isinstance(portal, dict):
        return False
    level = str(portal.get("level") or "").lower()
    state = str(portal.get("state") or "").lower()
    risk = _as_float(portal.get("risk"), 0.0)
    return risk >= 25 or level in {"watch", "medium", "high"} or state in {"watch", "suspicious", "failure_likely"}


def interpret_feedback(label: str, portal: dict[str, Any] | None, vision: dict[str, Any] | None) -> dict[str, Any]:
    """Classify a feedback click as true/false positive/negative.

    We intentionally judge the full Portal AI result and the raw vision result. Portal AI
    may warn because of stale telemetry even when the vision frame is fine, while the
    vision result may be warning before the overall risk score crosses a visible level.
    """
    kind = _feedback_kind(label)
    user_says_failure = kind == "failure"
    vision_warning = _vision_is_warning(vision)
    portal_warning = _portal_is_warning(portal)
    ai_was_warning = bool(vision_warning or portal_warning)
    if user_says_failure and ai_was_warning:
        outcome = "true_positive"
    elif user_says_failure and not ai_was_warning:
        outcome = "false_negative"
    elif not user_says_failure and ai_was_warning:
        outcome = "false_positive"
    elif not user_says_failure and not ai_was_warning:
        outcome = "true_negative"
    else:
        outcome = "unknown"
    return {
        "kind": kind,
        "user_says_failure": user_says_failure,
        "ai_was_warning": ai_was_warning,
        "portal_ai_was_warning": portal_warning,
        "vision_ai_was_warning": vision_warning,
        "outcome": outcome,
        "portal_risk_at_feedback": _as_int((portal or {}).get("risk"), 0) if isinstance(portal, dict) else 0,
        "portal_level_at_feedback": (portal or {}).get("level") if isinstance(portal, dict) else None,
        "vision_state_at_feedback": (vision or {}).get("visual_state") if isinstance(vision, dict) else None,
        "vision_bad_now_at_feedback": bool((vision or {}).get("bad_now")) if isinstance(vision, dict) else False,
        "vision_bad_confirmed_at_feedback": bool((vision or {}).get("bad_confirmed")) if isinstance(vision, dict) else False,
    }


def _status_file(status: dict[str, Any] | None) -> str:
    if not isinstance(status, dict):
        return ""
    file_name = str(status.get("file") or "").strip()
    return "" if file_name in {"", "-", "None", "none"} else file_name


def _active_print(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    if status.get("active_print"):
        return True
    state = str(status.get("status_text") or status.get("state") or "").lower()
    progress = _as_float(status.get("progress"), 0.0)
    file_name = _status_file(status)
    hot_target = _as_float(status.get("hotend_target"), 0.0)
    bed_target = _as_float(status.get("bed_target"), 0.0)
    return any(x in state for x in ["print", "paus", "resum", "filament", "extruder"]) or bool(file_name and progress < 99.9 and (hot_target > 0 or bed_target > 0))


def _load_suppressions() -> list[dict[str, Any]]:
    if not SUPPRESSIONS_PATH.exists():
        return []
    try:
        data = json.loads(SUPPRESSIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            now = time.time()
            return [x for x in data if isinstance(x, dict) and _as_float(x.get("expires_at_epoch"), 0) > now]
    except Exception:
        return []
    return []


def _save_suppressions(items: list[dict[str, Any]]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SUPPRESSIONS_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as exc:
        log("warning", f"Could not save AI feedback suppressions: {exc}", "portal_ai")


def current_suppressions(printer_id: str | None = None) -> list[dict[str, Any]]:
    items = _load_suppressions()
    if printer_id:
        return [x for x in items if x.get("printer_id") == printer_id]
    return items


def record_feedback_suppression(
    printer_id: str,
    label: str,
    interpretation: dict[str, Any],
    status: dict[str, Any] | None,
    portal: dict[str, Any] | None,
    vision: dict[str, Any] | None,
    fresh_heuristics: dict[str, Any] | None = None,
    ai_cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    ai_cfg = ai_cfg or {}
    if not ai_cfg.get("feedback_suppression_enabled", True):
        return None
    if str(interpretation.get("outcome") or "") not in FALSE_POSITIVE_OUTCOMES:
        return None
    if _feedback_kind(label) not in {"positive", "false_alarm"}:
        return None
    file_name = _status_file(status)
    if not file_name or not _active_print(status):
        return None

    heur = fresh_heuristics if isinstance(fresh_heuristics, dict) else _heuristics_from_vision(vision)
    warnings = _warning_list(vision, heur)
    failure_types = _failure_types(vision)
    visual_state = str((vision or {}).get("visual_state") or "").strip().lower() if isinstance(vision, dict) else ""
    if not warnings and not failure_types and not visual_state:
        return None

    now = time.time()
    ttl_hours = max(0.5, min(72.0, _as_float(ai_cfg.get("feedback_suppression_ttl_hours"), 18.0)))
    entry = {
        "id": uuid.uuid4().hex[:12],
        "printer_id": printer_id,
        "created_at_epoch": now,
        "expires_at_epoch": now + (ttl_hours * 3600.0),
        "label": str(label or ""),
        "outcome": str(interpretation.get("outcome") or ""),
        "file": file_name,
        "progress_at_feedback": _as_float((status or {}).get("progress"), 0.0) if isinstance(status, dict) else 0.0,
        "portal_risk_at_feedback": interpretation.get("portal_risk_at_feedback"),
        "match": {
            "visual_state": visual_state,
            "failure_types": failure_types,
            "heuristic_warnings": warnings,
            "possible_stringing": bool(heur.get("possible_stringing")) if isinstance(heur, dict) else False,
            "camera_bad": bool(heur.get("camera_bad")) if isinstance(heur, dict) else False,
        },
    }
    items = [x for x in _load_suppressions() if not (x.get("printer_id") == printer_id and x.get("file") == file_name and x.get("match") == entry["match"])]
    items.append(entry)
    _save_suppressions(items[-100:])
    return entry


def _overlap(a: list[str], b: list[str]) -> bool:
    return bool(set(a or []).intersection(set(b or [])))


def _similar_warning(entry: dict[str, Any], result: dict[str, Any]) -> bool:
    match = entry.get("match") if isinstance(entry.get("match"), dict) else {}
    heur = _heuristics_from_vision(result)
    current_warnings = _warning_list(result, heur)
    current_types = _failure_types(result)
    current_state = str(result.get("visual_state") or "").strip().lower()
    if _overlap(current_warnings, match.get("heuristic_warnings") or []):
        return True
    if _overlap(current_types, match.get("failure_types") or []):
        return True
    if current_state and current_state == str(match.get("visual_state") or ""):
        return True
    if bool(heur.get("possible_stringing")) and bool(match.get("possible_stringing")):
        return True
    if bool(heur.get("camera_bad")) and bool(match.get("camera_bad")):
        return True
    return False


def apply_feedback_suppression(printer_id: str, result: dict[str, Any], status: dict[str, Any] | None, ai_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    ai_cfg = ai_cfg or {}
    if not ai_cfg.get("feedback_suppression_enabled", True) or not isinstance(result, dict):
        return result
    file_name = _status_file(status)
    if not file_name or not _active_print(status):
        return result
    severity = _as_float(result.get("severity"), 0.0)
    max_severity = max(10.0, min(95.0, _as_float(ai_cfg.get("feedback_suppression_max_severity"), 65.0)))
    if severity > max_severity or str(result.get("visual_state") or "").lower() == "failure_likely":
        return result
    if str(result.get("visual_state") or "").lower() == "camera_bad" and not ai_cfg.get("feedback_suppression_include_camera", False):
        return result
    for entry in _load_suppressions():
        if entry.get("printer_id") != printer_id or entry.get("file") != file_name:
            continue
        if not _similar_warning(entry, result):
            continue
        out = dict(result)
        out["feedback_suppressed"] = {
            "id": entry.get("id"),
            "label": entry.get("label"),
            "outcome": entry.get("outcome"),
            "reason": "Similar warning was marked good/false alarm for this active print.",
            "expires_at_epoch": entry.get("expires_at_epoch"),
        }
        out["recommended_action"] = "keep_watching"
        out["severity"] = min(_as_int(out.get("severity"), 0), 24)
        out["confidence"] = min(_as_int(out.get("confidence"), 0), 55)
        if str(out.get("visual_state") or "").lower() in {"possible_failure", "failure_likely"}:
            out["visual_state"] = "uncertain"
        summary = str(out.get("summary") or "").strip()
        suffix = "Similar warning suppressed from your feedback for this print."
        out["summary"] = f"{summary} {suffix}".strip()
        heur = out.get("heuristics") if isinstance(out.get("heuristics"), dict) else {}
        warnings = heur.get("warnings") if isinstance(heur.get("warnings"), list) else []
        if "feedback_suppressed" not in warnings:
            warnings = list(warnings) + ["feedback_suppressed"]
        heur["warnings"] = warnings
        heur["feedback_suppressed"] = True
        out["heuristics"] = heur
        return out
    return result


def feedback_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels: dict[str, int] = {}
    kinds: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    printers: dict[str, int] = {}
    frame_count = 0
    for row in rows:
        label = str(row.get("label") or "unknown")
        labels[label] = labels.get(label, 0) + 1
        printer = str(row.get("printer_id") or "unknown")
        printers[printer] = printers.get(printer, 0) + 1
        snap = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        kind = str(snap.get("kind") or _feedback_kind(label))
        kinds[kind] = kinds.get(kind, 0) + 1
        interpretation = snap.get("interpretation") if isinstance(snap.get("interpretation"), dict) else {}
        outcome = str(interpretation.get("outcome") or "uninterpreted")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        frame = snap.get("frame") if isinstance(snap.get("frame"), dict) else {}
        if frame.get("captured"):
            frame_count += 1
    return {
        "labels": labels,
        "kinds": kinds,
        "outcomes": outcomes,
        "printers": printers,
        "frames": frame_count,
        "suppressions": len(_load_suppressions()),
    }
