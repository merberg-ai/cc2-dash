from __future__ import annotations

import base64
import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests

try:
    from PIL import Image, ImageFilter, ImageStat
except Exception:  # Pillow is optional at import time; requirements installs it for normal use.
    Image = ImageFilter = ImageStat = None  # type: ignore[assignment]

from .camera_proxy import camera_proxy_config, camera_relays
from .config import DATA_DIR, PrinterConfig
from .logger import log

DEFAULT_VISION_PROMPT = """You are monitoring a 3D printer camera image.

Classify the visible print state. Return JSON only using this schema:
{
  "visual_state": "ok | uncertain | possible_failure | failure_likely | camera_bad",
  "failure_types": ["spaghetti", "detached_part", "blob_on_nozzle", "first_layer_issue", "camera_blocked", "unknown"],
  "confidence": 0,
  "severity": 0,
  "summary": "short human explanation",
  "recommended_action": "keep_watching | inspect | pause_print | stop_print"
}

Be conservative. Do not call normal supports, purge towers, brims, skirts, infill, filament swaps, or multicolor purge waste a failure unless it is clearly abnormal. If the image is too dark, blurry, blocked, frozen-looking, or unusable, use camera_bad or uncertain rather than guessing."""

VISION_STATES = {"ok", "uncertain", "possible_failure", "failure_likely", "camera_bad"}
VISION_ACTIONS = {"keep_watching", "inspect", "pause_print", "stop_print"}


def _now_label() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


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


def _clamp(value: Any, lo: int = 0, hi: int = 100, default: int = 0) -> int:
    return max(lo, min(hi, _as_int(value, default)))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _mean_abs_diff(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 999.0
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


def _json_from_text(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    # Ollama models sometimes wrap JSON in markdown fences despite explicit JSON-only prompts.
    # Strip common fence formats before parsing.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("model response did not contain a JSON object")


class VisionMonitor:
    """Server-side camera frame sampler + Ollama vision second opinion.

    The monitor is intentionally stateful and conservative. It caches results so
    dashboard refreshes do not hammer the camera or Ollama, keeps the latest
    frame for display, and tracks consecutive bad checks before the rule engine
    treats vision as a serious signal.
    """

    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}

    def cached_result(self, printer_id: str) -> dict[str, Any] | None:
        result = (self._state.get(printer_id) or {}).get("last_result")
        return dict(result) if isinstance(result, dict) else None

    def reset(self, printer_id: str | None = None) -> None:
        if printer_id:
            self._state.pop(printer_id, None)
        else:
            self._state.clear()

    def latest_frame_path(self, printer_id: str) -> Path:
        return DATA_DIR / "vision" / printer_id / "latest.jpg"

    def _printer_urls(self, pcfg: PrinterConfig) -> list[str]:
        return [f"http://{pcfg.host}:8080/", f"http://{pcfg.host}:8080/?action=stream"]

    def _grab_frame(self, pcfg: PrinterConfig, timeout: float = 8.0, max_bytes: int = 5_000_000, app_cfg: dict[str, Any] | None = None, printer_id: str | None = None) -> bytes:
        proxy_cfg = camera_proxy_config(app_cfg or {})
        if proxy_cfg.get("enabled", True):
            relay = camera_relays.get(printer_id or pcfg.id, pcfg)
            try:
                return relay.latest_frame(proxy_cfg, max_age=float(proxy_cfg.get("stale_frame_seconds") or 10.0) * 3.0, wait_timeout=timeout)
            except Exception as exc:
                if not proxy_cfg.get("fallback_to_direct", False):
                    raise RuntimeError(f"Camera relay frame unavailable: {exc}")
                log("warning", f"Camera relay unavailable for vision; falling back to direct camera grab: {exc}", "camera", printer=printer_id or pcfg.id)

        headers = {
            "User-Agent": "cc2-dash-lite-vision",
            "Accept": "multipart/x-mixed-replace,image/jpeg,*/*",
            "Cache-Control": "no-cache",
        }
        last_error = "no camera URL tried"
        for url in self._printer_urls(pcfg):
            try:
                with requests.get(url, stream=True, timeout=(3.5, timeout), headers=headers) as resp:
                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code} from {url}"
                        continue
                    ctype = (resp.headers.get("content-type") or "").lower()
                    body = bytearray()
                    for chunk in resp.iter_content(chunk_size=16384):
                        if not chunk:
                            continue
                        body.extend(chunk)
                        start = body.find(b"\xff\xd8")
                        end = body.find(b"\xff\xd9", start + 2 if start >= 0 else 0)
                        if start >= 0 and end >= 0:
                            return bytes(body[start : end + 2])
                        if "image/jpeg" in ctype and len(body) > 2048 and len(body) < max_bytes:
                            # Some cameras return a single JPEG response with no multipart boundary.
                            end = body.find(b"\xff\xd9")
                            if body.startswith(b"\xff\xd8") and end >= 0:
                                return bytes(body[: end + 2])
                        if len(body) > max_bytes:
                            raise RuntimeError("camera frame exceeded maximum capture size")
                    if body.startswith(b"\xff\xd8"):
                        return bytes(body)
                    last_error = f"no JPEG frame found from {url}"
            except Exception as exc:
                last_error = str(exc)
        raise RuntimeError(f"Camera frame unavailable: {last_error}")

    def _save_frame(self, printer_id: str, frame: bytes, suspicious: bool = False, store_suspicious_only: bool = True, max_saved: int = 50) -> dict[str, Any]:
        root = DATA_DIR / "vision" / printer_id
        root.mkdir(parents=True, exist_ok=True)
        latest = root / "latest.jpg"
        latest.write_bytes(frame)
        saved_path = None
        if suspicious or not store_suspicious_only:
            saved_path = root / f"{'suspicious' if suspicious else 'frame'}_{_now_label()}.jpg"
            saved_path.write_bytes(frame)
            old = sorted(root.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
            # Always keep latest.jpg plus the newest N archived frames.
            archive = [p for p in old if p.name != "latest.jpg"]
            for p in archive[max(0, int(max_saved)) :]:
                try:
                    p.unlink()
                except Exception:
                    pass
        return {
            "latest_path": str(latest),
            "saved_path": str(saved_path) if saved_path else None,
            "latest_url": f"/api/printers/{printer_id}/vision/latest.jpg?ts={int(time.time())}",
            "bytes": len(frame),
        }

    def _analyze_frame(self, printer_id: str, frame: bytes, ai_cfg: dict[str, Any]) -> dict[str, Any]:
        """Cheap local frame checks that do not require Ollama.

        This is deliberately simple, fast, and explainable. Ollama is great as a
        second opinion, but dark frames and obvious fine-edge anomalies should not be
        missed just because a model decided to be chill about it.
        """
        metrics: dict[str, Any] = {
            "enabled": _as_bool(ai_cfg.get("vision_heuristics_enabled"), True),
            "warnings": [],
            "camera_bad": False,
            "possible_stringing": False,
            "summary": "Local frame heuristics not run.",
        }
        if not metrics["enabled"]:
            metrics["summary"] = "Local frame heuristics disabled."
            return metrics
        if Image is None or ImageStat is None or ImageFilter is None:
            metrics["summary"] = "Pillow is not installed; local frame heuristics skipped."
            return metrics

        try:
            img = Image.open(BytesIO(frame))
            img.load()
            gray = img.convert("L")
            gray.thumbnail((320, 240))
            stat = ImageStat.Stat(gray)
            mean = float(stat.mean[0])
            stddev = float(stat.stddev[0])
            hist = gray.histogram()
            pixels = max(1, gray.size[0] * gray.size[1])
            dark_px = sum(hist[:35]) / pixels
            bright_px = sum(hist[220:]) / pixels

            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_hist = edges.histogram()
            edge_threshold = _as_int(ai_cfg.get("vision_edge_threshold"), 34)
            edge_density = sum(edge_hist[max(0, min(255, edge_threshold)):]) / pixels

            tiny = gray.resize((64, 48))
            sample = tiny.tobytes()
            state = self._state.setdefault(printer_id, {})
            previous_sample = state.get("prev_luma_sample")
            previous_edge_density = _as_float(state.get("prev_edge_density"), edge_density)
            previous_mean = state.get("prev_mean_luma")
            baseline_mean = state.get("baseline_mean_luma")
            frame_delta = _mean_abs_diff(sample, previous_sample) if isinstance(previous_sample, (bytes, bytearray)) else None
            state["prev_luma_sample"] = sample
            state["prev_edge_density"] = edge_density
            state["prev_mean_luma"] = mean

            dark_mean_threshold = _as_float(ai_cfg.get("vision_dark_mean_threshold"), 58.0)
            dark_contrast_threshold = _as_float(ai_cfg.get("vision_dark_contrast_threshold"), 22.0)
            dark_relative_drop_threshold = _as_float(ai_cfg.get("vision_dark_relative_drop_threshold"), 18.0)
            stringing_edge_threshold = _as_float(ai_cfg.get("vision_stringing_edge_density_threshold"), 0.125)
            stringing_edge_delta_threshold = _as_float(ai_cfg.get("vision_stringing_edge_delta_threshold"), 0.045)
            freeze_delta_threshold = _as_float(ai_cfg.get("vision_freeze_delta_threshold"), 0.5)

            warnings: list[str] = []
            baseline_drop = None
            previous_drop = None
            if isinstance(baseline_mean, (int, float)):
                baseline_drop = float(baseline_mean) - mean
            if isinstance(previous_mean, (int, float)):
                previous_drop = float(previous_mean) - mean

            # CC2 cameras can still produce a visible image with the light off, so
            # absolute black-frame checks are not enough. Treat a dim + flat image,
            # or a major luma drop from the learned baseline, as camera_bad.
            dark_absolute = mean < dark_mean_threshold
            dark_flat = mean < max(dark_mean_threshold * 1.25, 72.0) and stddev < dark_contrast_threshold
            dark_relative = baseline_drop is not None and baseline_drop >= dark_relative_drop_threshold and mean < max(82.0, float(baseline_mean) * 0.72)
            dark_step = previous_drop is not None and previous_drop >= max(12.0, dark_relative_drop_threshold * 0.7) and mean < 82.0
            if dark_absolute or dark_flat or dark_relative or dark_step:
                warnings.append("dark_frame" if dark_absolute or dark_flat else "light_drop_detected")
                metrics["camera_bad"] = True
            elif stddev < max(8.0, dark_contrast_threshold * 0.55):
                warnings.append("low_contrast_frame")

            # Slowly learn a normal brightness baseline from usable frames. Do not
            # let dark/problem frames drag the baseline down.
            if not metrics["camera_bad"] and mean >= dark_mean_threshold:
                if isinstance(baseline_mean, (int, float)):
                    state["baseline_mean_luma"] = (float(baseline_mean) * 0.85) + (mean * 0.15)
                else:
                    state["baseline_mean_luma"] = mean

            if frame_delta is not None and frame_delta < freeze_delta_threshold:
                warnings.append("nearly_identical_frame")

            edge_delta = edge_density - previous_edge_density
            # Stringing/spaghetti tends to add lots of thin high-contrast edges.
            # This is a hint, not a verdict. It intentionally avoids firing in dark frames.
            if not metrics["camera_bad"] and mean >= dark_mean_threshold and edge_density >= stringing_edge_threshold:
                warnings.append("high_fine_edge_density")
                metrics["possible_stringing"] = True
            elif not metrics["camera_bad"] and mean >= dark_mean_threshold and edge_delta >= stringing_edge_delta_threshold and edge_density >= max(0.10, stringing_edge_threshold * 0.72):
                warnings.append("fine_edge_density_jump")
                metrics["possible_stringing"] = True

            metrics.update({
                "summary": "Local frame heuristics completed.",
                "warnings": warnings,
                "mean_luma": round(mean, 2),
                "contrast": round(stddev, 2),
                "dark_pixel_ratio": round(dark_px, 4),
                "bright_pixel_ratio": round(bright_px, 4),
                "edge_density": round(edge_density, 4),
                "edge_density_delta": round(edge_delta, 4),
                "frame_delta": round(float(frame_delta), 3) if frame_delta is not None else None,
                "baseline_luma": round(float(state.get("baseline_mean_luma")), 2) if isinstance(state.get("baseline_mean_luma"), (int, float)) else None,
                "baseline_luma_drop": round(float(baseline_drop), 2) if baseline_drop is not None else None,
                "previous_luma_drop": round(float(previous_drop), 2) if previous_drop is not None else None,
                "thresholds": {
                    "dark_mean": dark_mean_threshold,
                    "dark_contrast": dark_contrast_threshold,
                    "dark_relative_drop": dark_relative_drop_threshold,
                    "edge_density": stringing_edge_threshold,
                    "edge_delta": stringing_edge_delta_threshold,
                },
            })
            return metrics
        except Exception as exc:
            metrics.update({
                "summary": f"Local frame heuristics failed: {exc}",
                "warnings": ["heuristics_error"],
                "error": str(exc),
            })
            return metrics

    def _apply_heuristics(self, result: dict[str, Any], heuristics: dict[str, Any], ai_cfg: dict[str, Any]) -> dict[str, Any]:
        warnings = heuristics.get("warnings") or []
        if not isinstance(warnings, list):
            warnings = []
        result["heuristics"] = heuristics
        if not heuristics.get("enabled", True):
            return result

        if heuristics.get("camera_bad"):
            result.update({
                "visual_state": "camera_bad",
                "failure_types": sorted(set((result.get("failure_types") or []) + ["camera_blocked"])),
                "confidence": max(_as_int(result.get("confidence"), 0), 88),
                "severity": max(_as_int(result.get("severity"), 0), 52),
                "summary": f"Camera image looks too dark or low-contrast for reliable monitoring. Metrics: luma {heuristics.get('mean_luma')}, contrast {heuristics.get('contrast')}.",
                "recommended_action": "inspect",
            })
            return result

        if heuristics.get("possible_stringing"):
            edge_density = _as_float(heuristics.get("edge_density"), 0.0)
            edge_delta = _as_float(heuristics.get("edge_density_delta"), 0.0)
            current_state = str(result.get("visual_state") or "ok")
            if current_state == "ok":
                # Keep this conservative: fine-edge noise can be supports/infill, but it
                # deserves a visible warning and a log entry.
                state = "possible_failure" if edge_density >= _as_float(ai_cfg.get("vision_stringing_possible_failure_threshold"), 0.22) else "uncertain"
                result.update({
                    "visual_state": state,
                    "failure_types": sorted(set((result.get("failure_types") or []) + ["spaghetti", "unknown"])),
                    "confidence": max(_as_int(result.get("confidence"), 0), 58 if state == "uncertain" else 70),
                    "severity": max(_as_int(result.get("severity"), 0), 35 if state == "uncertain" else 55),
                    "summary": f"Local camera heuristics see unusually high fine-edge detail that can indicate stringing/spaghetti; verify visually. Edge density {edge_density:.3f}, change {edge_delta:.3f}.",
                    "recommended_action": "inspect",
                })
            else:
                result["summary"] = f"{result.get('summary') or current_state}. Local edge-density warning also triggered."
                result["failure_types"] = sorted(set((result.get("failure_types") or []) + ["spaghetti"]))
        return result

    def _apply_telemetry_guard(self, result: dict[str, Any], status: dict[str, Any] | None) -> dict[str, Any]:
        """Keep the vision model aligned with telemetry.

        Vision models often describe a still frame as "idle" because they cannot see
        motion. If MQTT says the printer is printing, we preserve the visual verdict but
        add a mismatch warning and rewrite the summary so the dashboard does not claim
        the printer is idle while the status card says Printing.
        """
        context = self._telemetry_context(status)
        result["telemetry_context"] = context
        result["telemetry_active_print"] = bool(context.get("active_print"))
        if not context.get("active_print"):
            return result
        summary = str(result.get("summary") or "")
        bad_words = ("idle", "ready to print", "not printing", "no active print", "standby", "ready")
        if any(w in summary.lower() for w in bad_words):
            heur = result.get("heuristics") if isinstance(result.get("heuristics"), dict) else {}
            warnings = heur.get("warnings") if isinstance(heur.get("warnings"), list) else []
            if "telemetry_model_mismatch" not in warnings:
                warnings.append("telemetry_model_mismatch")
            heur["warnings"] = warnings
            heur["telemetry_model_mismatch"] = True
            result["heuristics"] = heur
            result["visual_state"] = "uncertain" if result.get("visual_state") == "ok" else str(result.get("visual_state") or "uncertain")
            result["confidence"] = max(_as_int(result.get("confidence"), 0), 35)
            result["severity"] = max(_as_int(result.get("severity"), 0), 10)
            result["summary"] = (
                f"Telemetry says this printer is actively printing ({context.get('progress')}%). "
                "The vision model described the scene as idle/ready, so treat this result as uncertain and inspect the image."
            )
            result["recommended_action"] = "inspect"
        return result

    def _log_vision_event(self, printer_id: str, result: dict[str, Any]) -> None:
        state = self._state.setdefault(printer_id, {})
        heur = result.get("heuristics") if isinstance(result.get("heuristics"), dict) else {}
        warnings = tuple(sorted(str(x) for x in (heur.get("warnings") or [])))
        signature = (
            result.get("visual_state"),
            bool(result.get("bad_now")),
            bool(result.get("bad_confirmed")),
            warnings,
            int(_as_float(result.get("consecutive_bad"), 0)),
        )
        if state.get("last_logged_signature") == signature:
            return
        state["last_logged_signature"] = signature
        visual_state = str(result.get("visual_state") or "unknown")
        if visual_state in {"ok", "standby", "pending"} and not warnings:
            return
        level = "warning" if visual_state in {"camera_bad", "possible_failure", "failure_likely"} or result.get("bad_now") else "info"
        bits = []
        if warnings:
            bits.append("heuristics=" + ",".join(warnings))
        if result.get("confidence") is not None:
            bits.append(f"conf={result.get('confidence')}%")
        if result.get("severity") is not None:
            bits.append(f"severity={result.get('severity')}%")
        if result.get("consecutive_bad"):
            bits.append(f"bad={result.get('consecutive_bad')}/{result.get('required_bad_checks')}")
        msg = f"Vision {visual_state}: {result.get('summary') or 'No summary'}"
        if bits:
            msg += " (" + " · ".join(bits) + ")"
        log(level, msg, "vision", printer=printer_id, vision={k: v for k, v in result.items() if k not in {"raw_model_text"}})

    def _telemetry_context(self, status: dict[str, Any] | None) -> dict[str, Any]:
        status = status or {}
        state_text = str(status.get("status_text") or status.get("state") or "unknown")
        progress = _as_float(status.get("progress"), 0.0)
        hot_target = _as_float(status.get("hotend_target"), 0.0)
        bed_target = _as_float(status.get("bed_target"), 0.0)
        file_name = str(status.get("file") or "-")
        active = bool(
            status.get("active_print")
            or any(x in state_text.lower() for x in ["print", "paus", "resum", "filament", "extruder", "stopp"])
            or (file_name != "-" and progress < 99.9 and (hot_target > 0 or bed_target > 0))
        )
        return {
            "active_print": active,
            "state": state_text,
            "progress": round(progress, 1),
            "file": file_name,
            "hotend_target": status.get("hotend_target"),
            "hotend_current": status.get("hotend_current"),
            "bed_target": status.get("bed_target"),
            "bed_current": status.get("bed_current"),
            "speed_setting": status.get("speed_setting"),
        }

    def _context_prompt(self, ai_cfg: dict[str, Any], status: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        base_prompt = str(ai_cfg.get("vision_prompt") or DEFAULT_VISION_PROMPT)
        context = self._telemetry_context(status)
        active_note = "ACTIVE PRINT" if context.get("active_print") else "NOT CURRENTLY ACTIVE"
        context_text = (
            "\n\nPrinter telemetry context follows. Trust this telemetry for printer state; "
            "do not infer idle/ready from the camera image if telemetry says printing.\n"
            f"Telemetry state: {active_note}\n"
            f"Status: {context.get('state')}\n"
            f"Progress: {context.get('progress')}%\n"
            f"File: {context.get('file')}\n"
            f"Hotend: {context.get('hotend_current')}/{context.get('hotend_target')} C\n"
            f"Bed: {context.get('bed_current')}/{context.get('bed_target')} C\n"
            f"Speed setting: {context.get('speed_setting') or '-'}\n"
            "Look for visual print problems only: spaghetti/stringing, detached part, blob on nozzle, failed first layer, severe darkness/camera blockage. "
            "If the image is too dark to judge, return visual_state camera_bad. "
            "Return JSON only."
        )
        return base_prompt + context_text, context

    def _ollama_chat(self, ai_cfg: dict[str, Any], frame: bytes, status: dict[str, Any] | None = None) -> dict[str, Any]:
        base_url = str(ai_cfg.get("ollama_base_url") or ai_cfg.get("ollama_host") or "http://localhost:11434").rstrip("/")
        model = str(ai_cfg.get("ollama_vision_model") or "llava").strip() or "llava"
        prompt, telemetry_context = self._context_prompt(ai_cfg, status)
        timeout = max(5.0, min(180.0, _as_float(ai_cfg.get("ollama_timeout_seconds"), 45.0)))
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(frame).decode("ascii")],
                }
            ],
            "options": {
                "temperature": 0,
            },
        }
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        content = ((data.get("message") or {}).get("content") or data.get("response") or "").strip()
        parsed = _json_from_text(content)
        parsed["raw_model_text"] = content[:2000]
        parsed["model"] = model
        parsed["ollama_base_url"] = base_url
        parsed["telemetry_context"] = telemetry_context
        return parsed

    def _normalize_model_result(self, raw: dict[str, Any]) -> dict[str, Any]:
        state = str(raw.get("visual_state") or raw.get("state") or "uncertain").strip().lower()
        if state not in VISION_STATES:
            state = "uncertain"
        action = str(raw.get("recommended_action") or "keep_watching").strip().lower()
        if action not in VISION_ACTIONS:
            action = "inspect" if state in {"possible_failure", "failure_likely"} else "keep_watching"
        failure_types = raw.get("failure_types") or raw.get("failure_type") or []
        if isinstance(failure_types, str):
            failure_types = [failure_types]
        if not isinstance(failure_types, list):
            failure_types = []
        return {
            "visual_state": state,
            "failure_types": [str(x).strip() for x in failure_types if str(x).strip()][:8],
            "confidence": _clamp(raw.get("confidence"), 0, 100, 0),
            "severity": _clamp(raw.get("severity"), 0, 100, 0),
            "summary": str(raw.get("summary") or state.replace("_", " ").title())[:500],
            "recommended_action": action,
            "model": raw.get("model"),
            "ollama_base_url": raw.get("ollama_base_url"),
            "raw_model_text": raw.get("raw_model_text"),
            "telemetry_context": raw.get("telemetry_context"),
        }

    def check(self, printer_id: str, pcfg: PrinterConfig, cfg: dict[str, Any], status: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
        ai_cfg = cfg.get("portal_ai", {}) or {}
        now = time.time()
        state = self._state.setdefault(printer_id, {"consecutive_bad": 0})
        enabled = bool(ai_cfg.get("vision_ai_enabled", False))
        if not enabled:
            result = {
                "enabled": False,
                "visual_state": "disabled",
                "summary": "Vision AI is disabled.",
                "last_check_epoch": now,
                "last_check": time.strftime("%H:%M:%S"),
            }
            state["last_result"] = result
            return result

        active_print = bool((status or {}).get("portal_ai", {}).get("active_print") or (status or {}).get("active_print"))
        if ai_cfg.get("vision_require_active_print", True) and status is not None:
            # The rule engine may not be attached yet, so fall back to simple status hints.
            state_text = str(status.get("state") or status.get("status_text") or "").lower()
            file_name = str(status.get("file") or "-")
            progress = _as_float(status.get("progress"), 0.0)
            hot_target = _as_float(status.get("hotend_target"), 0.0)
            bed_target = _as_float(status.get("bed_target"), 0.0)
            active_print = active_print or any(x in state_text for x in ["print", "paus", "resum", "filament", "extruder"]) or bool(file_name != "-" and progress < 99.9 and (hot_target > 0 or bed_target > 0))
            if not active_print and not force:
                cached = state.get("last_result")
                result = {
                    "enabled": True,
                    "skipped": True,
                    "visual_state": "standby",
                    "summary": "No active print; vision check skipped.",
                    "consecutive_bad": 0,
                    "last_check_epoch": now,
                    "last_check": time.strftime("%H:%M:%S"),
                    "previous": cached if isinstance(cached, dict) else None,
                }
                state["consecutive_bad"] = 0
                state["last_result"] = result
                return result

        interval = max(10.0, min(3600.0, _as_float(ai_cfg.get("vision_check_interval_seconds"), 120.0)))
        cached = state.get("last_result")
        if not force and isinstance(cached, dict) and now - _as_float(cached.get("last_check_epoch"), 0.0) < interval:
            out = dict(cached)
            out["served_from_cache"] = True
            return out

        frame_info: dict[str, Any] = {}
        try:
            frame = self._grab_frame(pcfg, timeout=_as_float(ai_cfg.get("vision_frame_timeout_seconds"), 8.0), app_cfg=cfg, printer_id=printer_id)
            heuristics = self._analyze_frame(printer_id, frame, ai_cfg)
            skip_ollama = bool(heuristics.get("camera_bad") and _as_bool(ai_cfg.get("vision_skip_ollama_on_bad_frame"), True))
            if skip_ollama:
                result = {
                    "visual_state": "camera_bad",
                    "failure_types": ["camera_blocked"],
                    "confidence": 90,
                    "severity": 55,
                    "summary": "Local frame checks say the camera image is too dark or low-contrast for reliable monitoring.",
                    "recommended_action": "inspect",
                    "model": "local-heuristics",
                    "ollama_base_url": str(ai_cfg.get("ollama_base_url") or ai_cfg.get("ollama_host") or "http://localhost:11434"),
                    "raw_model_text": "",
                }
            else:
                raw = self._ollama_chat(ai_cfg, frame, status=status)
                result = self._normalize_model_result(raw)
            result = self._apply_heuristics(result, heuristics, ai_cfg)
            result = self._apply_telemetry_guard(result, status)
            confidence_threshold = _as_int(ai_cfg.get("vision_confidence_threshold"), 70)
            severity_threshold = _as_int(ai_cfg.get("vision_severity_threshold"), 60)
            suspicious = (
                result["visual_state"] in {"possible_failure", "failure_likely"}
                and result["confidence"] >= confidence_threshold
                and result["severity"] >= severity_threshold
            )
            if result["visual_state"] == "camera_bad":
                suspicious = True
            if result.get("heuristics", {}).get("possible_stringing") and result["visual_state"] in {"uncertain", "possible_failure"}:
                suspicious = suspicious or bool(ai_cfg.get("vision_heuristic_warnings_count_as_bad", True))
            if suspicious:
                state["consecutive_bad"] = int(state.get("consecutive_bad") or 0) + 1
            elif result["visual_state"] == "ok":
                state["consecutive_bad"] = 0
            else:
                state["consecutive_bad"] = max(0, int(state.get("consecutive_bad") or 0) - 1)

            frame_info = self._save_frame(
                printer_id,
                frame,
                suspicious=suspicious or bool((result.get("heuristics") or {}).get("warnings")),
                store_suspicious_only=bool(ai_cfg.get("vision_store_suspicious_only", True)),
                max_saved=_as_int(ai_cfg.get("vision_max_saved_frames"), 50),
            )
            required = max(1, _as_int(ai_cfg.get("vision_required_bad_checks", ai_cfg.get("require_multiple_bad_checks", 2)), 2))
            result.update(
                {
                    "enabled": True,
                    "ok": True,
                    "bad_now": bool(suspicious),
                    "bad_confirmed": int(state.get("consecutive_bad") or 0) >= required,
                    "consecutive_bad": int(state.get("consecutive_bad") or 0),
                    "required_bad_checks": required,
                    "frame": frame_info,
                    "last_check_epoch": now,
                    "last_check": time.strftime("%H:%M:%S"),
                }
            )
            self._log_vision_event(printer_id, result)
        except Exception as exc:
            state["consecutive_bad"] = int(state.get("consecutive_bad") or 0) + 1
            required = max(1, _as_int(ai_cfg.get("vision_required_bad_checks", ai_cfg.get("require_multiple_bad_checks", 2)), 2))
            result = {
                "enabled": True,
                "ok": False,
                "visual_state": "camera_bad",
                "failure_types": ["camera_blocked"],
                "confidence": 0,
                "severity": 20,
                "summary": f"Vision check failed: {exc}",
                "recommended_action": "inspect",
                "bad_now": True,
                "bad_confirmed": int(state.get("consecutive_bad") or 0) >= required,
                "consecutive_bad": int(state.get("consecutive_bad") or 0),
                "required_bad_checks": required,
                "frame": frame_info,
                "last_error": str(exc),
                "last_check_epoch": now,
                "last_check": time.strftime("%H:%M:%S"),
            }
        self._log_vision_event(printer_id, result)
        state["last_result"] = result
        try:
            root = DATA_DIR / "vision" / printer_id
            root.mkdir(parents=True, exist_ok=True)
            with (root / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
        return dict(result)

    def list_ollama_models(self, ai_cfg: dict[str, Any]) -> dict[str, Any]:
        base_url = str(ai_cfg.get("ollama_base_url") or ai_cfg.get("ollama_host") or "http://localhost:11434").rstrip("/")
        timeout = max(3.0, min(30.0, _as_float(ai_cfg.get("ollama_timeout_seconds"), 10.0)))
        resp = requests.get(f"{base_url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        models = []
        for item in data.get("models") or []:
            name = item.get("name") or item.get("model")
            if name:
                models.append(str(name))
        return {"ok": True, "base_url": base_url, "models": models, "raw": data}

    def pull_ollama_model(self, ai_cfg: dict[str, Any], model: str) -> dict[str, Any]:
        base_url = str(ai_cfg.get("ollama_base_url") or ai_cfg.get("ollama_host") or "http://localhost:11434").rstrip("/")
        model = str(model or "").strip()
        if not model:
            raise ValueError("model name is required")
        # Model pulls can take a while, especially on first download. Keep the
        # request non-streaming so the frontend gets one clean JSON response.
        timeout = max(60.0, min(1800.0, _as_float(ai_cfg.get("ollama_pull_timeout_seconds"), 900.0)))
        resp = requests.post(f"{base_url}/api/pull", json={"model": model, "stream": False}, timeout=timeout)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:2000]}
        models = []
        try:
            models = self.list_ollama_models({**ai_cfg, "ollama_base_url": base_url}).get("models", [])
        except Exception:
            pass
        return {"ok": True, "base_url": base_url, "model": model, "pull": data, "models": models}


vision_monitor = VisionMonitor()
