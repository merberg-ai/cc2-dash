from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CC2_DATA_DIR", APP_ROOT / "data"))
CONFIG_PATH = Path(os.environ.get("CC2_CONFIG", DATA_DIR / "config.json"))


@dataclass
class PrinterConfig:
    id: str
    name: str
    host: str
    serial: str
    access_code: str = ""
    port: int = 1883
    enabled: bool = True
    allow_commands: bool = True
    allow_dangerous_commands: bool = False


def safe_printer_id(name_or_serial: str) -> str:
    out = []
    for ch in str(name_or_serial or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in ("-", "_", " ", "."):
            out.append("-")
    value = "".join(out).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value or "cc2-printer"


def printer_dict_to_config(printer_id: str, data: dict[str, Any]) -> PrinterConfig:
    host = data.get("host") or data.get("ip") or ""
    serial = data.get("serial") or data.get("sn") or data.get("printer_id") or printer_id or host
    return PrinterConfig(
        id=str(printer_id or safe_printer_id(serial or host)),
        name=str(data.get("name") or data.get("host_name") or "Centauri Carbon 2"),
        host=str(host),
        serial=str(serial),
        access_code=str(data.get("access_code") or data.get("pin") or ""),
        port=int(data.get("port") or 1883),
        enabled=bool(data.get("enabled", True)),
        allow_commands=bool(data.get("allow_commands", True)),
        allow_dangerous_commands=bool(data.get("allow_dangerous_commands", False)),
    )


def public_printer_dict(cfg: PrinterConfig, include_secret: bool = False) -> dict[str, Any]:
    data = asdict(cfg)
    data["portal_url"] = f"/portal-fullscreen?printer={cfg.id}"
    data["portal_chrome_url"] = f"/portal?printer={cfg.id}"
    data["kiosk_url"] = f"/kiosk?printer={cfg.id}"
    data["direct_portal_url"] = f"http://{cfg.host}/"
    data["camera_url"] = f"/api/printers/{cfg.id}/camera/stream"
    data["direct_camera_url"] = f"http://{cfg.host}:8080/"
    if not include_secret:
        data.pop("access_code", None)
    data["access_code_set"] = bool(cfg.access_code)
    return data

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": 4,
    "app": {
        "name": "cc2-dash",
        "bind_host": "0.0.0.0",
        "port": 8088,
        "default_printer": None,
        "theme": "octo_dark_blue",
        "setup_complete": False,
    },
    "network": {
        "allow_mode": "subnet",
        "allowed_subnets": ["192.168.1.0/24"],
        "allowed_hosts": [],
        "always_allow_localhost": True,
        "scan_ports": [80, 8080, 3030, 1883, 8899],
    },
    "appearance": {
        "font_pack": "Terminal Modern",
        "font_scale": "normal",
        "letter_spacing": "normal",
        "uppercase_buttons": False,
        "fonts": {
            "base": "Terminal Modern",
            "heading": "Terminal Modern",
            "number": "Terminal Modern",
            "button": "Terminal Modern",
        },
    },
    "printers": {},
    "features": {
        "file_manager_enabled": False,
        "filament_manager_enabled": False,
        "kiosk_enabled": True,
    },
    "kiosk": {
        "refresh_interval_seconds": 3,
        "camera_fit": "contain",
        "show_top_nav": True,
        "show_printer_name": True,
        "show_camera_badge": True,
        "show_progress": True,
        "show_ai_status": True,
        "show_time_left": True,
        "show_print_status": True,
    },
    "camera_proxy": {
        "enabled": True,
        "start_on_boot": True,
        "max_client_fps": 8,
        "upstream_connect_timeout_seconds": 5,
        "upstream_read_timeout_seconds": 20,
        "stale_frame_seconds": 10,
        "idle_shutdown_seconds": 120,
        "fallback_to_direct": False,
        "rewrite_portal_camera_urls": True,
        "log_client_connects": False,
    },
    "portal_ai": {
        "enabled": True,
        "background_monitor_enabled": True,
        "check_interval_seconds": 30,
        "background_log_changes": True,
        "background_min_log_level": "watch",
        "monitor_active_prints_only": True,
        "telemetry_rules_enabled": True,
        "camera_rules_enabled": True,
        "opencv_rules_enabled": False,
        "vision_ai_enabled": False,
        "ollama_base_url": "http://192.168.1.24:11434",
        "ollama_vision_model": "llava",
        "ollama_timeout_seconds": 45,
        "vision_check_interval_seconds": 120,
        "vision_frame_timeout_seconds": 8,
        "vision_require_active_print": True,
        "vision_heuristics_enabled": True,
        "vision_dark_mean_threshold": 58,
        "vision_dark_contrast_threshold": 22,
        "vision_dark_relative_drop_threshold": 18,
        "vision_stringing_edge_density_threshold": 0.125,
        "vision_stringing_edge_delta_threshold": 0.045,
        "vision_heuristic_warnings_count_as_bad": True,
        "vision_skip_ollama_on_bad_frame": True,
        "vision_confidence_threshold": 70,
        "vision_severity_threshold": 60,
        "vision_required_bad_checks": 2,
        "vision_store_suspicious_only": True,
        "vision_max_saved_frames": 50,
        "vision_treat_benign_uncertain_as_ok": True,
        "vision_benign_uncertain_max_severity": 25,
        "vision_uncertain_risk_severity_threshold": 35,
        "vision_prompt": "You are monitoring a 3D printer camera image. Return JSON only with visual_state, failure_types, confidence, severity, summary, and recommended_action. If the print appears normal and you do not see a visible problem, return visual_state ok, not uncertain. Only return uncertain when the image is genuinely ambiguous, blurry, blocked, too dark, or shows something unclear that could be a failure. If you return uncertain, explain what is ambiguous. Be conservative and do not treat normal supports, purge towers, brims, skirts, infill, filament swaps, multicolor purge waste, reflections, or ordinary filament color changes as failure unless clearly abnormal.",
        "progress_stuck_minutes": 8,
        "multi_color_mode": "auto",
        "multi_color_progress_stuck_minutes": 30,
        "stale_status_seconds": 75,
        "feedback_enabled": True,
        "feedback_suppression_enabled": True,
        "feedback_suppression_ttl_hours": 18,
        "feedback_suppression_max_severity": 65,
        "feedback_suppression_include_camera": False,
        "feedback_threshold_auto_tuning_enabled": False,
        "ai_feedback_learning_enabled": True,
        "ai_feedback_learning_mode": "suggest_only",
        "ai_learning_min_samples": 8,
        "ai_learning_min_false_positives": 4,
        "ai_learning_min_false_negatives": 2,
        "ai_learning_max_dark_luma_adjustment": 8,
        "ai_learning_max_edge_density_adjustment": 0.05,
        "ai_learning_max_required_bad_checks_adjustment": 1,
        "ai_learning_apply_dark_luma": True,
        "ai_learning_apply_edge_density": True,
        "ai_learning_apply_required_bad_checks": True,
        "ai_learning_rebuild_on_feedback": True,
        "ai_learning_keep_jsonl_audit_log": True,
        "auto_pause_enabled": False,
        "auto_pause_threshold": 90,
        "require_multiple_bad_checks": 3
    },
    "dashboard": {
        "refresh_interval_seconds": 3,
        "camera_autoload": True,
        "show_footer": True,
        "compact_mode": False,
        "show_gcode_thumbnail": True,
        "cards": [
            {"id": "camera_status", "label": "Camera + Status", "enabled": True, "order": 10},
            {"id": "quick_actions", "label": "Quick Actions", "enabled": True, "order": 20},
            {"id": "connection_info", "label": "Connection", "enabled": True, "order": 30},
        ],
    },
    "actions": {
        "light_toggle": {
            "label": "Light Toggle",
            "enabled": True,
            "visible": True,
            "order": 10,
            "style": "primary",
            "requires_confirm": False,
            "confirm_text": "Toggle the printer light?",
            "spinner_text": "Sending light command...",
        },
        "pause_resume": {
            "label": "Pause / Resume Print",
            "enabled": True,
            "visible": True,
            "order": 20,
            "style": "primary",
            "requires_confirm": False,
            "confirm_text": "Pause or resume the current print?",
            "spinner_text": "Sending pause/resume...",
        },
        "cancel_print": {
            "label": "Cancel Print",
            "enabled": True,
            "visible": True,
            "order": 30,
            "style": "danger",
            "requires_confirm": True,
            "confirm_text": "Cancel the current print? This cannot be undone.",
            "spinner_text": "Canceling print...",
        },
        "restart_camera": {
            "label": "Restart Camera Stream",
            "enabled": True,
            "visible": True,
            "order": 40,
            "style": "secondary",
            "requires_confirm": False,
            "confirm_text": "Restart the camera stream?",
            "spinner_text": "Restarting camera...",
        },
        "vision_check_now": {
            "label": "Analyze Camera Now",
            "enabled": True,
            "visible": True,
            "order": 45,
            "style": "primary",
            "requires_confirm": False,
            "confirm_text": "Run an Ollama vision check now?",
            "spinner_text": "Analyzing camera...",
        },
        "set_speed_preset": {
            "label": "Set Speed",
            "enabled": True,
            "visible": True,
            "order": 50,
            "style": "primary",
            "requires_confirm": False,
            "confirm_text": "Change print speed preset?",
            "spinner_text": "Setting speed...",
        },
    },
    "effects": {
        "fade_in_cards": True,
        "button_spinners": True,
        "loading_skeletons": True,
        "toast_notifications": True,
        "status_dot_pulse": True,
    },
    "advanced": {
        "adapter": "generic_elegoo",
        "request_timeout_seconds": 2.5,
        "portal_proxy_enabled": False,
        "command_endpoints": {},
        "status_paths": ["/api/status", "/status", "/printer/status"],
    },
}


def deep_merge(defaults: Any, loaded: Any) -> Any:
    if isinstance(defaults, dict) and isinstance(loaded, dict):
        out = copy.deepcopy(defaults)
        for key, value in loaded.items():
            out[key] = deep_merge(out.get(key), value) if key in out else value
        return out
    return copy.deepcopy(loaded if loaded is not None else defaults)


def migrate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Small compatibility fixes for older saved config files."""
    try:
        old_version = int(cfg.get("config_version") or 1)
    except Exception:
        old_version = 1
    try:
        # v1.2.26: keep the experimental File Manager route available, but hide
        # its top-nav entry by default. Older saved configs inherited the prior
        # True default, so migrate once; users can re-enable it from Settings.
        features = cfg.setdefault("features", {})
        if old_version < 2:
            features["file_manager_enabled"] = False
        # v1.2.28: Filament Manager is still experimental. Keep the route and
        # settings available, but hide the top-nav entry by default until the
        # filament/CANVAS support is ready for normal use.
        if old_version < 3:
            features["filament_manager_enabled"] = False
        dashboard = cfg.setdefault("dashboard", {})
        dashboard.setdefault("show_gcode_thumbnail", True)
        cfg["config_version"] = 4
    except Exception:
        pass
    try:
        speed = ((cfg.get("actions") or {}).get("set_speed_preset") or {})
        speed.pop("preset_mode", None)
        speed.pop("preset_name", None)
        label = str(speed.get("label") or "")
        if label.lower().startswith("set speed:"):
            speed["label"] = "Set Speed"
    except Exception:
        pass
    try:
        # v1.2.4 shipped conservative darkness defaults that missed the CC2
        # lights-off case. If a saved config still has those exact defaults, move
        # it to the more useful v1.2.5 thresholds while preserving custom values.
        ai = cfg.setdefault("portal_ai", {})
        if ai.get("vision_dark_mean_threshold") in (None, 42, 42.0):
            ai["vision_dark_mean_threshold"] = 58
        if ai.get("vision_dark_contrast_threshold") in (None, 18, 18.0):
            ai["vision_dark_contrast_threshold"] = 22
        ai.setdefault("vision_dark_relative_drop_threshold", 18)
    except Exception:
        pass
    try:
        features = cfg.setdefault("features", {})
        features.setdefault("kiosk_enabled", True)
        kiosk = cfg.setdefault("kiosk", {})
        kiosk.setdefault("refresh_interval_seconds", 3)
        kiosk.setdefault("camera_fit", "contain")
        kiosk.setdefault("show_top_nav", True)
        kiosk.setdefault("show_printer_name", True)
        kiosk.setdefault("show_camera_badge", True)
        kiosk.setdefault("show_progress", True)
        kiosk.setdefault("show_ai_status", True)
        kiosk.setdefault("show_time_left", True)
        kiosk.setdefault("show_print_status", True)
    except Exception:
        pass
    try:
        ai = cfg.setdefault("portal_ai", {})
        ai.setdefault("monitor_active_prints_only", True)
        ai.setdefault("vision_treat_benign_uncertain_as_ok", True)
        ai.setdefault("vision_benign_uncertain_max_severity", 25)
        ai.setdefault("vision_uncertain_risk_severity_threshold", 35)
        ai.setdefault("feedback_suppression_enabled", True)
        ai.setdefault("feedback_suppression_ttl_hours", 18)
        ai.setdefault("feedback_suppression_max_severity", 65)
        ai.setdefault("feedback_suppression_include_camera", False)
        ai.setdefault("feedback_threshold_auto_tuning_enabled", False)
        ai.setdefault("ai_feedback_learning_enabled", True)
        ai.setdefault("ai_feedback_learning_mode", "suggest_only")
        ai.setdefault("ai_learning_min_samples", 8)
        ai.setdefault("ai_learning_min_false_positives", 4)
        ai.setdefault("ai_learning_min_false_negatives", 2)
        ai.setdefault("ai_learning_max_dark_luma_adjustment", 8)
        ai.setdefault("ai_learning_max_edge_density_adjustment", 0.05)
        ai.setdefault("ai_learning_max_required_bad_checks_adjustment", 1)
        ai.setdefault("ai_learning_apply_dark_luma", True)
        ai.setdefault("ai_learning_apply_edge_density", True)
        ai.setdefault("ai_learning_apply_required_bad_checks", True)
        ai.setdefault("ai_learning_rebuild_on_feedback", True)
        ai.setdefault("ai_learning_keep_jsonl_audit_log", True)
        mode = str(ai.get("ai_feedback_learning_mode") or "suggest_only").strip().lower()
        if mode not in {"off", "suggest_only", "auto_adjust_safe"}:
            ai["ai_feedback_learning_mode"] = "suggest_only"
        app_cfg = cfg.setdefault("app", {})
        if app_cfg.get("name") == "cc2-dash-lite":
            app_cfg["name"] = "cc2-dash"
        old_prompt = "You are monitoring a 3D printer camera image. Return JSON only with visual_state, failure_types, confidence, severity, summary, and recommended_action. Be conservative and do not treat normal supports, purge towers, brims, skirts, infill, filament swaps, or multicolor purge waste as failure unless clearly abnormal."
        current_prompt = str(ai.get("vision_prompt") or "").strip()
        if not current_prompt or current_prompt == old_prompt:
            ai["vision_prompt"] = str(DEFAULT_CONFIG["portal_ai"]["vision_prompt"])
    except Exception:
        pass
    return cfg


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_data_dir()
    if not CONFIG_PATH.exists():
        return migrate_config(copy.deepcopy(DEFAULT_CONFIG))
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except Exception:
        backup = CONFIG_PATH.with_suffix(".broken.json")
        try:
            CONFIG_PATH.replace(backup)
        except Exception:
            pass
        return migrate_config(copy.deepcopy(DEFAULT_CONFIG))
    return migrate_config(deep_merge(DEFAULT_CONFIG, loaded))


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    ensure_data_dir()
    merged = migrate_config(deep_merge(DEFAULT_CONFIG, cfg))
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
    tmp.replace(CONFIG_PATH)
    return merged


def needs_setup(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_config()
    printers = cfg.get("printers") or {}
    if not cfg.get("app", {}).get("setup_complete") or not printers:
        return True
    # Older cc2-dash builds could save only host/URL. The CC2 MQTT bridge
    # needs both serial number and PIN/access code, so route back through setup
    # until at least one configured printer has pairing details.
    for p in printers.values():
        if p.get("host") and p.get("serial") and p.get("access_code"):
            return False
    return True


def sorted_cards(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(cfg.get("dashboard", {}).get("cards", []), key=lambda x: int(x.get("order", 999)))


def sorted_actions(cfg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    actions = cfg.get("actions", {})
    return sorted(actions.items(), key=lambda kv: int(kv[1].get("order", 999)))


def default_printer(cfg: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    printers = cfg.get("printers", {}) or {}
    wanted = cfg.get("app", {}).get("default_printer")
    if wanted and wanted in printers:
        return wanted, printers[wanted]
    if printers:
        pid = next(iter(printers.keys()))
        return pid, printers[pid]
    return None, None
