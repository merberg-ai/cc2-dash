from __future__ import annotations

import json
import math
import time
from statistics import median
from typing import Any

from . import ai_learning_db as db
from .config import DEFAULT_CONFIG
from .logger import log

LEARNING_MODES = {"off", "suggest_only", "auto_adjust_safe"}
DARK_FLAGS = {"dark_frame", "light_drop_detected", "low_contrast_frame"}
EDGE_FLAGS = {"high_fine_edge_density", "fine_edge_density_jump"}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _as_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None or value == "":
            return default
        value = float(value)
        if math.isnan(value):
            return default
        return value
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _mode(ai_cfg: dict[str, Any] | None) -> str:
    value = str((ai_cfg or {}).get("ai_feedback_learning_mode") or "suggest_only").strip().lower()
    return value if value in LEARNING_MODES else "suggest_only"


def _is_enabled(ai_cfg: dict[str, Any] | None) -> bool:
    ai_cfg = ai_cfg or {}
    if bool(ai_cfg.get("ai_feedback_learning_enabled", True)) is False:
        return False
    return _mode(ai_cfg) != "off"


def _numeric_list(rows: list[dict[str, Any]], key: str) -> list[float]:
    vals: list[float] = []
    for row in rows:
        val = _as_float(row.get(key), None)
        if val is not None:
            vals.append(float(val))
    return sorted(vals)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return round(vals[0], 4)
    idx = (len(vals) - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return round(vals[lo], 4)
    frac = idx - lo
    return round(vals[lo] * (1 - frac) + vals[hi] * frac, 4)


def _decode_flags(row: dict[str, Any]) -> set[str]:
    raw = row.get("triggered_flags")
    try:
        if isinstance(raw, str):
            data = json.loads(raw) if raw.strip().startswith("[") else [raw]
        elif isinstance(raw, list):
            data = raw
        else:
            data = []
    except Exception:
        data = []
    return {str(x).strip() for x in data if str(x).strip()}


def _print_stage(progress: float | None, active: bool = True) -> str:
    if not active:
        return "idle"
    if progress is None:
        return "unknown"
    if progress < 5:
        return "first_layer"
    if progress < 20:
        return "early"
    if progress < 80:
        return "middle"
    if progress < 100:
        return "late"
    return "unknown"


def _manual_thresholds(ai_cfg: dict[str, Any] | None) -> dict[str, Any]:
    ai_cfg = ai_cfg or {}
    defaults = DEFAULT_CONFIG.get("portal_ai", {})
    return {
        "dark_luma": _as_float(ai_cfg.get("vision_dark_mean_threshold", defaults.get("vision_dark_mean_threshold", 58)), 58.0),
        "edge_density": _as_float(ai_cfg.get("vision_stringing_edge_density_threshold", defaults.get("vision_stringing_edge_density_threshold", 0.125)), 0.125),
        "required_bad_checks": _as_int(ai_cfg.get("vision_required_bad_checks", defaults.get("vision_required_bad_checks", 2)), 2),
    }


def _confidence(sample_count: int, fp_count: int, fn_count: int, ai_cfg: dict[str, Any]) -> str:
    if sample_count < 4:
        return "none"
    if sample_count >= 20 and (fp_count >= 4 or fn_count >= 3):
        return "high"
    if sample_count >= _as_int(ai_cfg.get("ai_learning_min_samples"), 8) and (fp_count or fn_count):
        return "medium"
    return "low"


def _heuristics_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    fresh = snapshot.get("fresh_heuristics")
    if isinstance(fresh, dict) and fresh:
        return fresh
    vision = snapshot.get("vision")
    if isinstance(vision, dict):
        heur = vision.get("heuristics")
        if isinstance(heur, dict):
            return heur
    portal = snapshot.get("portal_ai")
    if isinstance(portal, dict):
        vision = portal.get("vision")
        if isinstance(vision, dict):
            heur = vision.get("heuristics")
            if isinstance(heur, dict):
                return heur
    return {}


def _timestamp_to_iso(epoch: Any) -> str:
    val = _as_float(epoch, None)
    if val is None:
        return _now_iso()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(val)))


def _active_status(status: dict[str, Any]) -> bool:
    if status.get("active_print") is not None:
        return bool(status.get("active_print"))
    file_name = str(status.get("file") or "").strip()
    progress = _as_float(status.get("progress"), None)
    state = str(status.get("state") or status.get("status_text") or "").lower()
    return bool(file_name and file_name not in {"-", "none", "None"}) or any(x in state for x in ("print", "paus", "filament", "extruder")) or (progress is not None and 0 < progress < 100)


def sample_from_feedback_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert the existing JSONL feedback row into a structured SQLite sample."""
    snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
    status = snapshot.get("status") if isinstance(snapshot.get("status"), dict) else {}
    portal = snapshot.get("portal_ai") if isinstance(snapshot.get("portal_ai"), dict) else {}
    vision = snapshot.get("vision") if isinstance(snapshot.get("vision"), dict) else {}
    heur = _heuristics_from_snapshot(snapshot)
    interpretation = snapshot.get("interpretation") if isinstance(snapshot.get("interpretation"), dict) else {}
    frame = snapshot.get("frame") if isinstance(snapshot.get("frame"), dict) else {}
    suppression = snapshot.get("suppression") if isinstance(snapshot.get("suppression"), dict) else None

    progress = _as_float(status.get("progress"), None)
    flags = heur.get("warnings") if isinstance(heur.get("warnings"), list) else []
    return {
        "created_at": _timestamp_to_iso(row.get("timestamp") or snapshot.get("created_at_epoch")),
        "printer_id": str(row.get("printer_id") or snapshot.get("printer_id") or "unknown"),
        "feedback_label": str(row.get("label") or snapshot.get("label") or "unknown"),
        "feedback_note": str(row.get("note") or snapshot.get("note") or ""),
        "outcome": interpretation.get("outcome"),
        "ai_was_warning": bool(interpretation.get("ai_was_warning")),
        "user_says_failure": bool(interpretation.get("user_says_failure")),
        "file_name": status.get("file"),
        "print_stage": _print_stage(progress, active=_active_status(status)),
        "progress_percent": progress,
        "risk_score": _as_float(portal.get("risk"), None),
        "severity": _as_float(vision.get("severity", portal.get("severity")), None),
        "confidence": _as_float(vision.get("confidence", portal.get("confidence")), None),
        "vision_state": vision.get("visual_state") or vision.get("state") or portal.get("state"),
        "dark_luma": _as_float(heur.get("mean_luma"), None),
        "contrast": _as_float(heur.get("contrast"), None),
        "edge_density": _as_float(heur.get("edge_density"), None),
        "edge_delta": _as_float(heur.get("edge_density_delta"), None),
        "triggered_flags": flags,
        "suppression_match": bool(suppression),
        "model_name": str((snapshot.get("client_context") or {}).get("model_name") or "") if isinstance(snapshot.get("client_context"), dict) else "",
        "prompt_version": "cc2-ai-feedback-v3",
        "frame_path": frame.get("relative_path") or frame.get("path"),
        "raw_json": row,
    }


def record_feedback_row(row: dict[str, Any]) -> dict[str, Any]:
    sample = sample_from_feedback_row(row)
    return db.insert_feedback_sample(sample)


def rebuild_profile(printer_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or {}
    ai_cfg = cfg.get("portal_ai", {}) if isinstance(cfg.get("portal_ai"), dict) else {}
    samples = db.fetch_samples(printer_id, limit=5000)
    sample_count = len(samples)
    counts = {
        "true_positive": sum(1 for s in samples if s.get("outcome") == "true_positive"),
        "false_positive": sum(1 for s in samples if s.get("outcome") == "false_positive"),
        "false_negative": sum(1 for s in samples if s.get("outcome") == "false_negative"),
        "true_negative": sum(1 for s in samples if s.get("outcome") == "true_negative"),
    }
    fp_rows = [s for s in samples if s.get("outcome") == "false_positive"]
    fn_rows = [s for s in samples if s.get("outcome") == "false_negative"]

    min_samples = _as_int(ai_cfg.get("ai_learning_min_samples"), 8)
    min_fp = _as_int(ai_cfg.get("ai_learning_min_false_positives"), 4)
    min_fn = _as_int(ai_cfg.get("ai_learning_min_false_negatives"), 2)
    max_dark = float(_as_float(ai_cfg.get("ai_learning_max_dark_luma_adjustment"), 8.0) or 8.0)
    max_edge = float(_as_float(ai_cfg.get("ai_learning_max_edge_density_adjustment"), 0.05) or 0.05)
    max_bad_checks = max(0, min(_as_int(ai_cfg.get("ai_learning_max_required_bad_checks_adjustment"), 1), 3))

    fp_dark = sum(1 for s in fp_rows if _decode_flags(s).intersection(DARK_FLAGS))
    fn_dark = sum(1 for s in fn_rows if _decode_flags(s).intersection(DARK_FLAGS) or _as_float(s.get("dark_luma"), 999) < _as_float(ai_cfg.get("vision_dark_mean_threshold"), 58))
    fp_edge = sum(1 for s in fp_rows if _decode_flags(s).intersection(EDGE_FLAGS))
    fn_edge = sum(1 for s in fn_rows if _decode_flags(s).intersection(EDGE_FLAGS) or _as_float(s.get("edge_density"), 0) >= (_as_float(ai_cfg.get("vision_stringing_edge_density_threshold"), 0.125) or 0.125) * 0.75)

    suggested_dark = 0.0
    suggested_edge = 0.0
    suggested_bad = 0
    reasons: list[str] = []

    if sample_count < min_samples:
        reasons.append(f"No modifier suggested yet: {sample_count}/{min_samples} samples collected.")
    else:
        if bool(ai_cfg.get("ai_learning_apply_dark_luma", True)):
            if fp_dark >= min_fp:
                suggested_dark -= min(max_dark, 1.0 + (fp_dark - min_fp) * 0.75)
                reasons.append(f"Dark threshold suggestion decreased because {fp_dark} dark/low-light warnings were marked false alarm or good.")
            if fn_dark >= min_fn:
                suggested_dark += min(max_dark, 1.0 + (fn_dark - min_fn) * 0.75)
                reasons.append(f"Dark threshold suggestion increased because {fn_dark} missed-failure samples involved dark/low-contrast frames.")
        if bool(ai_cfg.get("ai_learning_apply_edge_density", True)):
            if fp_edge >= min_fp:
                suggested_edge += min(max_edge, 0.01 + (fp_edge - min_fp) * 0.005)
                reasons.append(f"Edge threshold suggestion increased because {fp_edge} high-edge warnings were marked false alarm or good.")
            if fn_edge >= min_fn:
                suggested_edge -= min(max_edge, 0.01 + (fn_edge - min_fn) * 0.005)
                reasons.append(f"Edge threshold suggestion decreased because {fn_edge} missed-failure samples were near edge/stringing thresholds.")
        if bool(ai_cfg.get("ai_learning_apply_required_bad_checks", True)) and counts["false_positive"] >= min_fp and max_bad_checks > 0:
            suggested_bad = min(max_bad_checks, 1)
            reasons.append(f"Required bad checks suggestion increased because {counts['false_positive']} warnings were marked false alarms or good.")

    suggested_dark = round(_clamp(suggested_dark, -max_dark, max_dark), 4)
    suggested_edge = round(_clamp(suggested_edge, -max_edge, max_edge), 4)
    suggested_bad = max(0, min(max_bad_checks, int(suggested_bad)))

    mode = _mode(ai_cfg)
    enabled = _is_enabled(ai_cfg)
    if mode == "auto_adjust_safe" and enabled:
        applied_dark = suggested_dark
        applied_edge = suggested_edge
        applied_bad = suggested_bad
    else:
        applied_dark = 0.0
        applied_edge = 0.0
        applied_bad = 0

    normal_rows = [s for s in samples if s.get("outcome") == "true_negative" or str(s.get("feedback_label") or "") == "looks_good"]
    lumas = _numeric_list(normal_rows, "dark_luma")
    contrasts = _numeric_list(normal_rows, "contrast")
    edges = _numeric_list(normal_rows, "edge_density")
    confidence = _confidence(sample_count, counts["false_positive"], counts["false_negative"], ai_cfg)
    if not reasons:
        reasons.append("No modifier suggested yet: feedback does not show a repeated pattern.")

    profile = {
        "printer_id": printer_id,
        "updated_at": _now_iso(),
        "learning_mode": mode,
        "confidence": confidence,
        "sample_count": sample_count,
        "true_positive_count": counts["true_positive"],
        "false_positive_count": counts["false_positive"],
        "false_negative_count": counts["false_negative"],
        "true_negative_count": counts["true_negative"],
        "dark_luma_modifier": applied_dark,
        "edge_density_modifier": applied_edge,
        "required_bad_checks_modifier": applied_bad,
        "suggested_dark_luma_modifier": suggested_dark,
        "suggested_edge_density_modifier": suggested_edge,
        "suggested_required_bad_checks_modifier": suggested_bad,
        "normal_luma_median": round(float(median(lumas)), 4) if lumas else None,
        "normal_luma_p10": _percentile(lumas, 0.10),
        "normal_luma_p90": _percentile(lumas, 0.90),
        "normal_contrast_median": round(float(median(contrasts)), 4) if contrasts else None,
        "normal_contrast_p10": _percentile(contrasts, 0.10),
        "normal_contrast_p90": _percentile(contrasts, 0.90),
        "normal_edge_density_median": round(float(median(edges)), 4) if edges else None,
        "normal_edge_density_p90": _percentile(edges, 0.90),
        "normal_edge_density_p95": _percentile(edges, 0.95),
        "raw_json": {
            "enabled": enabled,
            "counts": counts,
            "clusters": {"fp_dark": fp_dark, "fn_dark": fn_dark, "fp_edge": fp_edge, "fn_edge": fn_edge},
            "reasons": reasons,
            "bounds": {"dark_luma": max_dark, "edge_density": max_edge, "required_bad_checks": max_bad_checks},
        },
    }
    db.upsert_profile(profile)
    log("info", f"AI learning profile rebuilt for {printer_id}: {sample_count} samples, confidence={confidence}", "portal_ai", printer=printer_id)
    return profile_status(printer_id, cfg=cfg)


def reset_profile(printer_id: str | None = None, delete_samples: bool = False) -> dict[str, Any]:
    return db.reset_profile(printer_id, delete_samples=delete_samples)


def ensure_database() -> dict[str, Any]:
    return db.ensure_database()


def db_health() -> dict[str, Any]:
    return db.health()


def get_effective_ai_thresholds(printer_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or {}
    ai_cfg = cfg.get("portal_ai", {}) if isinstance(cfg.get("portal_ai"), dict) else {}
    manual = _manual_thresholds(ai_cfg)
    profile = db.get_profile(printer_id) or {}
    mode = _mode(ai_cfg)
    enabled = _is_enabled(ai_cfg)
    suggested = {
        "dark_luma_modifier": _as_float(profile.get("suggested_dark_luma_modifier"), 0.0) or 0.0,
        "edge_density_modifier": _as_float(profile.get("suggested_edge_density_modifier"), 0.0) or 0.0,
        "required_bad_checks_modifier": _as_int(profile.get("suggested_required_bad_checks_modifier"), 0),
    }
    if enabled and mode == "auto_adjust_safe":
        applied = dict(suggested)
    else:
        applied = {"dark_luma_modifier": 0.0, "edge_density_modifier": 0.0, "required_bad_checks_modifier": 0}
    effective = {
        "dark_luma": round(float(manual["dark_luma"] or 0) + float(applied["dark_luma_modifier"]), 4),
        "edge_density": round(float(manual["edge_density"] or 0) + float(applied["edge_density_modifier"]), 4),
        "required_bad_checks": max(1, int(manual["required_bad_checks"] or 1) + int(applied["required_bad_checks_modifier"] or 0)),
    }
    return {
        "manual": manual,
        "suggested": suggested,
        "applied": applied,
        "effective": effective,
        "mode": mode,
        "enabled": enabled,
        "confidence": profile.get("confidence") or "none",
        "sample_count": int(profile.get("sample_count") or 0),
    }


def _profile_raw(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {}
    raw = profile.get("raw_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def profile_status(printer_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or {}
    ai_cfg = cfg.get("portal_ai", {}) if isinstance(cfg.get("portal_ai"), dict) else {}
    profile = db.get_profile(printer_id)
    thresholds = get_effective_ai_thresholds(printer_id, cfg)
    raw = _profile_raw(profile)
    if not profile:
        profile = {
            "printer_id": printer_id,
            "confidence": "none",
            "sample_count": 0,
            "true_positive_count": 0,
            "false_positive_count": 0,
            "false_negative_count": 0,
            "true_negative_count": 0,
        }
    mode = _mode(ai_cfg)
    if mode == "off" or not _is_enabled(ai_cfg):
        message = "Persistent AI learning is off. Feedback can still be logged for review."
    elif mode == "suggest_only":
        message = "Suggest-only mode is active. Learned modifiers are not applied to live detection."
    else:
        message = "Auto-adjust-safe mode is active. Only bounded learned modifiers are applied; manual settings are unchanged."
    return {
        "ok": True,
        "printer_id": printer_id,
        "enabled": _is_enabled(ai_cfg),
        "mode": mode,
        "confidence": profile.get("confidence") or "none",
        "sample_count": int(profile.get("sample_count") or 0),
        "outcomes": {
            "true_positive": int(profile.get("true_positive_count") or 0),
            "false_positive": int(profile.get("false_positive_count") or 0),
            "false_negative": int(profile.get("false_negative_count") or 0),
            "true_negative": int(profile.get("true_negative_count") or 0),
        },
        "thresholds": thresholds,
        "normal_baselines": {
            "luma": {"median": profile.get("normal_luma_median"), "p10": profile.get("normal_luma_p10"), "p90": profile.get("normal_luma_p90")},
            "contrast": {"median": profile.get("normal_contrast_median"), "p10": profile.get("normal_contrast_p10"), "p90": profile.get("normal_contrast_p90")},
            "edge_density": {"median": profile.get("normal_edge_density_median"), "p90": profile.get("normal_edge_density_p90"), "p95": profile.get("normal_edge_density_p95")},
        },
        "reasons": raw.get("reasons") or [],
        "clusters": raw.get("clusters") or {},
        "updated_at": profile.get("updated_at"),
        "message": message,
    }


def known_printer_ids(cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or {}
    return sorted(set(list((cfg.get("printers") or {}).keys()) + db.list_printer_ids()))


def global_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or {}
    printers = known_printer_ids(cfg)
    profiles = [profile_status(pid, cfg) for pid in printers]
    return {
        "ok": True,
        "database": db.health(),
        "learning": {
            "enabled": _is_enabled((cfg.get("portal_ai") or {}) if isinstance(cfg, dict) else {}),
            "mode": _mode((cfg.get("portal_ai") or {}) if isinstance(cfg, dict) else {}),
            "profile_count": len(profiles),
            "profiles": profiles,
        },
    }
