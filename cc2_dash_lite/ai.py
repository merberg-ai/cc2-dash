from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Deque

from .config import DATA_DIR


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _text_contains(text: str, *needles: str) -> bool:
    hay = (text or "").lower()
    return any(n.lower() in hay for n in needles)


class PortalAIDetector:
    """Small explainable rule engine for cc2-dash-lite Portal AI.

    This intentionally starts boring and reliable: printer telemetry, MQTT freshness,
    temperature sanity, progress movement, and camera availability hints. Vision / OpenCV
    can be bolted on later without changing the UI contract.
    """

    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}
        self._feedback: Deque[dict[str, Any]] = deque(maxlen=200)

    def reset(self, printer_id: str | None = None) -> None:
        if printer_id:
            self._state.pop(printer_id, None)
        else:
            self._state.clear()

    def feedback(self, printer_id: str, label: str, note: str = "", snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        row = {
            "printer_id": printer_id,
            "label": str(label or "unknown"),
            "note": str(note or ""),
            "timestamp": time.time(),
            "snapshot": snapshot or {},
        }
        self._feedback.append(row)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with (DATA_DIR / "ai_feedback.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            # Feedback should never break the dashboard. Keep the in-memory label even
            # if disk persistence fails.
            row["persist_error"] = str(exc)
        return row

    def recent_feedback(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._feedback)[-max(1, min(limit, 200)):]

    def cached_result(self, printer_id: str) -> dict[str, Any] | None:
        result = (self._state.get(printer_id) or {}).get("last_result")
        return dict(result) if isinstance(result, dict) else None

    def evaluate(self, printer_id: str, status: dict[str, Any], snap: dict[str, Any] | None, cfg: dict[str, Any] | None = None, source: str = "request") -> dict[str, Any]:
        cfg = cfg or {}
        ai_cfg = cfg.get("portal_ai", {}) or {}
        if not ai_cfg.get("enabled", True):
            return {
                "enabled": False,
                "state": "disabled",
                "level": "disabled",
                "risk": 0,
                "summary": "Disabled",
                "reasons": ["Portal AI is disabled in settings."],
                "last_check_epoch": time.time(),
                "last_check": time.strftime("%H:%M:%S"),
                "source": source,
            }

        now = time.time()
        prev = self._state.setdefault(printer_id, {})
        reasons: list[str] = []
        positives: list[str] = []
        risk = 0

        snap = snap or {}
        normalized = snap.get("normalized") or {}
        state_text = str(status.get("status_text") or status.get("state") or normalized.get("sub_state") or normalized.get("state") or "unknown")
        state_lower = state_text.lower()
        reachable = bool(status.get("reachable"))
        connected = bool(status.get("connected"))
        registered = bool(status.get("registered"))
        message_age = _as_float(status.get("updated_at"), 999999.0)

        progress = _as_float(status.get("progress"), 0.0)
        file_name = str(status.get("file") or "-").strip()
        hotend_current = _as_float(status.get("hotend_current"), 0.0)
        hotend_target = _as_float(status.get("hotend_target"), 0.0)
        bed_current = _as_float(status.get("bed_current"), 0.0)
        bed_target = _as_float(status.get("bed_target"), 0.0)
        elapsed = (normalized.get("time") or {}).get("elapsed_sec")
        elapsed_sec = _as_float(elapsed, 0.0)
        exceptions = normalized.get("exceptions") or []
        camera_info = (normalized.get("external") or {}).get("camera")
        camera_attr = (normalized.get("attributes") or {}).get("camera_connected")
        filament = normalized.get("filament") or {}
        status_code = normalized.get("status_code")
        sub_status_code = normalized.get("sub_status_code")

        multi_color_mode = str(ai_cfg.get("multi_color_mode", "auto") or "auto").lower()
        multi_color_grace_minutes = _as_float(ai_cfg.get("multi_color_progress_stuck_minutes"), 30.0)
        filament_operation_state = (
            _text_contains(state_lower, "filament operating", "extruder preheating")
            or status_code in (3, 4, 13)
            or sub_status_code in (1045, 1096)
        )
        multi_color_grace_active = multi_color_mode == "always" or (multi_color_mode == "auto" and filament_operation_state)

        active_state = _text_contains(state_lower, "print", "paus", "resum", "stopp", "idle in print", "filament operating", "extruder preheating")
        has_print_markers = bool(file_name and file_name != "-" and progress < 99.9 and (hotend_target > 0 or bed_target > 0 or elapsed_sec > 0))
        active_print = bool(active_state or has_print_markers)

        if not reachable:
            risk += 45 if active_print else 30
            reasons.append("Printer is not reachable through the CC2 MQTT client.")
        elif not connected:
            risk += 30
            reasons.append("MQTT client is not fully connected yet.")
        elif not registered:
            risk += 25
            reasons.append("MQTT client is connected but registration is not confirmed yet.")
        else:
            positives.append("Printer telemetry is connected.")

        stale_after = _as_float(ai_cfg.get("stale_status_seconds"), 75.0)
        if message_age > stale_after:
            bump = 45 if active_print else 25
            risk += bump
            reasons.append(f"Last printer status is stale ({int(message_age)}s old).")
        elif message_age < 999999:
            positives.append(f"Telemetry is fresh ({int(message_age)}s old).")

        if _text_contains(state_lower, "error", "fail", "emergency", "exception"):
            risk += 80
            reasons.append(f"Printer state reports a problem: {state_text}.")
        if _text_contains(state_lower, "stopped") and 0 < progress < 99:
            risk += 65
            reasons.append("Print appears stopped before reaching 100% completion.")
        if _text_contains(state_lower, "paused"):
            if multi_color_grace_active:
                positives.append("Pause/filament operation detected; using multi-color grace handling.")
            else:
                risk += 12
                reasons.append("Print is paused. This may be intentional, but it needs attention.")
        if exceptions:
            risk += 45
            reasons.append(f"Printer reported exception status: {exceptions}.")

        if active_print:
            positives.append("A print appears to be active or recently active.")
            if hotend_target <= 0 and progress < 99:
                risk += 35
                reasons.append("Hotend target is off while the print still appears active.")
            elif hotend_target >= 150:
                diff = hotend_target - hotend_current
                if diff > 35:
                    risk += 45
                    reasons.append(f"Hotend is far below target ({hotend_current:.1f}/{hotend_target:.1f}°C).")
                elif diff > 20:
                    risk += 22
                    reasons.append(f"Hotend is below target ({hotend_current:.1f}/{hotend_target:.1f}°C).")
                else:
                    positives.append("Hotend temperature is near target.")

            if bed_target >= 35:
                diff = bed_target - bed_current
                if diff > 18:
                    risk += 25
                    reasons.append(f"Bed is well below target ({bed_current:.1f}/{bed_target:.1f}°C).")
                elif diff <= 10:
                    positives.append("Bed temperature is near target.")

            if filament.get("sensor_enabled") and filament.get("detected") is False:
                risk += 80
                reasons.append("Filament sensor reports no filament while printing.")

            stuck_minutes = _as_float(ai_cfg.get("progress_stuck_minutes"), 8.0)
            effective_stuck_minutes = stuck_minutes
            if multi_color_grace_active:
                effective_stuck_minutes = max(stuck_minutes, multi_color_grace_minutes)
            last_progress = prev.get("progress")
            changed_at = prev.get("progress_changed_at") or now
            if last_progress is None or abs(progress - _as_float(last_progress)) >= 0.15:
                prev["progress"] = progress
                prev["progress_changed_at"] = now
                prev["file"] = file_name
                positives.append("Progress has moved recently.")
            else:
                stuck_for = (now - changed_at) / 60.0
                if progress > 0.1 and multi_color_grace_active and stuck_for < effective_stuck_minutes:
                    positives.append(f"Progress is unchanged, but multi-color/filament-swap grace is active ({stuck_for:.1f}/{effective_stuck_minutes:.0f}m).")
                elif progress > 0.1 and stuck_for >= effective_stuck_minutes:
                    risk += 28 if multi_color_grace_active else 45
                    if multi_color_grace_active:
                        reasons.append(f"Progress has not changed for about {stuck_for:.1f} minutes, beyond the multi-color grace window.")
                    else:
                        reasons.append(f"Progress has not changed for about {stuck_for:.1f} minutes.")
                elif progress > 0.1 and stuck_for >= max(2.0, effective_stuck_minutes / 2.0):
                    risk += 8 if multi_color_grace_active else 18
                    if multi_color_grace_active:
                        reasons.append(f"Progress has been unchanged for about {stuck_for:.1f} minutes during multi-color grace.")
                    else:
                        reasons.append(f"Progress has been unchanged for about {stuck_for:.1f} minutes.")
        else:
            prev["progress"] = progress
            prev["progress_changed_at"] = now
            if reachable:
                positives.append("Printer is not reporting an active print.")

        if ai_cfg.get("camera_rules_enabled", True):
            # This is deliberately a lightweight camera health hint. The browser still
            # displays the stream; the vision monitor below handles actual frame analysis.
            camera_known = camera_info is not None or camera_attr is not None
            camera_bad = False
            if isinstance(camera_info, dict):
                camera_bad = camera_info.get("status") in (False, 0, "0", "off", "offline") or camera_info.get("connected") is False
            elif camera_info in (False, 0, "0", "off", "offline"):
                camera_bad = True
            if camera_attr in (False, 0, "0", "false", "False"):
                camera_bad = True
            if camera_bad:
                risk += 18 if active_print else 8
                reasons.append("Printer reports the camera may be unavailable.")
            elif camera_known:
                positives.append("Camera status hint looks okay.")

        vision_result = status.get("vision_ai") if isinstance(status.get("vision_ai"), dict) else None
        if ai_cfg.get("vision_ai_enabled", False) and vision_result:
            visual_state = str(vision_result.get("visual_state") or "unknown")
            confidence = _as_float(vision_result.get("confidence"), 0.0)
            severity = _as_float(vision_result.get("severity"), 0.0)
            summary_text = str(vision_result.get("summary") or visual_state.replace("_", " "))
            heuristics = vision_result.get("heuristics") if isinstance(vision_result.get("heuristics"), dict) else {}
            heur_warnings = heuristics.get("warnings") if isinstance(heuristics.get("warnings"), list) else []
            confirmed = bool(vision_result.get("bad_confirmed"))
            bad_now = bool(vision_result.get("bad_now"))
            consecutive = int(_as_float(vision_result.get("consecutive_bad"), 0.0))
            required = int(_as_float(vision_result.get("required_bad_checks"), 2.0))

            if vision_result.get("skipped"):
                positives.append("Vision check is standing by until an active print is detected.")
            elif not vision_result.get("ok", True):
                bump = 18 if active_print else 8
                if confirmed:
                    bump += 10
                risk += bump
                reasons.append(f"Vision check could not verify the camera image: {summary_text}")
            elif visual_state == "ok":
                positives.append("Ollama vision check says the camera view looks OK.")
            elif visual_state == "uncertain":
                bump = 8 if active_print else 3
                if bad_now and heuristics.get("possible_stringing"):
                    bump += 14 if active_print else 6
                    reasons.append(f"Local vision heuristics flagged possible stringing/spaghetti ({', '.join(map(str, heur_warnings))}): {summary_text}")
                else:
                    reasons.append(f"Ollama vision is uncertain: {summary_text}")
                risk += bump
            elif visual_state == "camera_bad":
                risk += 20 if active_print else 10
                reasons.append(f"Ollama vision reports a camera/view problem: {summary_text}")
            elif visual_state in ("possible_failure", "failure_likely"):
                base = 22 if visual_state == "possible_failure" else 38
                confidence_factor = max(0.4, min(1.0, confidence / 100.0))
                severity_factor = max(0.4, min(1.0, severity / 100.0))
                bump = int(round(base * ((confidence_factor + severity_factor) / 2.0)))
                if confirmed:
                    bump += 18 if visual_state == "failure_likely" else 10
                    reasons.append(f"Ollama vision {visual_state.replace('_', ' ')} after {consecutive}/{required} bad checks: {summary_text}")
                elif bad_now:
                    reasons.append(f"Ollama vision saw a possible issue ({consecutive}/{required} checks): {summary_text}")
                else:
                    reasons.append(f"Ollama vision noted a possible issue: {summary_text}")
                risk += bump

        risk = max(0, min(100, int(round(risk))))
        if risk >= 75:
            level = "high"
            state = "failure_likely"
            summary = "Failure Likely"
        elif risk >= 50:
            level = "medium"
            state = "suspicious"
            summary = "Suspicious"
        elif risk >= 25:
            level = "watch"
            state = "watch"
            summary = "Watching Closely"
        else:
            level = "low"
            state = "watching" if active_print else "standing_by"
            summary = "Watching" if active_print else "Standing By"

        if not reasons:
            reasons = positives[:3] or ["No warning rules are currently triggered."]
        else:
            # Add one positive hint for context when something is mildly wrong.
            if positives and risk < 75:
                reasons.append(positives[0])

        result = {
            "enabled": True,
            "state": state,
            "level": level,
            "risk": risk,
            "summary": summary,
            "reasons": reasons[:5],
            "positives": positives[:5],
            "active_print": active_print,
            "multi_color_grace_active": bool(multi_color_grace_active),
            "multi_color_mode": multi_color_mode,
            "progress_stuck_threshold_minutes": effective_stuck_minutes if active_print else _as_float(ai_cfg.get("progress_stuck_minutes"), 8.0),
            "last_check_epoch": now,
            "last_check": time.strftime("%H:%M:%S"),
            "source": source,
            "background_monitor_enabled": bool(ai_cfg.get("background_monitor_enabled", True)),
            "rules": {
                "telemetry": bool(ai_cfg.get("telemetry_rules_enabled", True)),
                "camera": bool(ai_cfg.get("camera_rules_enabled", True)),
                "vision": bool(ai_cfg.get("vision_ai_enabled", False)),
            },
            "vision": vision_result,
        }
        prev["last_result"] = result
        return result


portal_ai = PortalAIDetector()
