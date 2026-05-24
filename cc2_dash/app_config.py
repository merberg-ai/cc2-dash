from __future__ import annotations

import copy
import json
import os
import secrets
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict

DEFAULT_APP_CONFIG_PATH = Path(os.environ.get("CC2_DASH_APP_CONFIG", "config/app.json"))

DEFAULT_PERMISSIONS: Dict[str, Dict[str, bool]] = {
    "guest": {
        "view_dashboard": True,
        "view_camera": True,
        "view_temperatures": True,
        "view_files": False,
        "view_timelapse": True,
        "control_print": False,
        "set_temperatures": False,
        "set_fans": False,
        "start_print": False,
        "delete_files": False,
        "edit_settings": False,
        "dangerous_commands": False,
        "developer_console": False,
        "stock_portal": False,
    },
    "viewer": {
        "view_dashboard": True,
        "view_camera": True,
        "view_temperatures": True,
        "view_files": True,
        "view_timelapse": True,
        "control_print": False,
        "set_temperatures": False,
        "set_fans": False,
        "start_print": False,
        "delete_files": False,
        "edit_settings": False,
        "dangerous_commands": False,
        "developer_console": False,
        "stock_portal": False,
    },
    "operator": {
        "view_dashboard": True,
        "view_camera": True,
        "view_temperatures": True,
        "view_files": True,
        "view_timelapse": True,
        "control_print": True,
        "set_temperatures": True,
        "set_fans": True,
        "start_print": True,
        "delete_files": False,
        "edit_settings": False,
        "dangerous_commands": False,
        "developer_console": False,
        "stock_portal": False,
    },
    "admin": {
        "view_dashboard": True,
        "view_camera": True,
        "view_temperatures": True,
        "view_files": True,
        "view_timelapse": True,
        "control_print": True,
        "set_temperatures": True,
        "set_fans": True,
        "start_print": True,
        "delete_files": True,
        "edit_settings": True,
        "dangerous_commands": True,
        "developer_console": True,
        "stock_portal": True,
    },
}

DEFAULT_APP_CONFIG: Dict[str, Any] = {
    "config_version": 2,
    "server": {
        "host": "0.0.0.0",
        "port": 8088,
        "cors_mode": "same-origin",
        "allowed_origins": [],
    },
    "auth": {
        "enabled": True,
        "allow_guest_dashboard": True,
        "session_timeout_minutes": 720,
        "lockout_enabled": True,
        "max_failed_attempts": 8,
        "lockout_minutes": 10,
        "secure_cookie": False,
        "session_secret": "",
    },
    "guest_dashboard": {
        "show_camera": True,
        "show_current_job": True,
        "show_temperatures": True,
        "show_progress": True,
        "show_eta": True,
        "show_files": False,
        "show_timelapse": True,
        "mask_file_names": False,
        "show_printer_name": True,
        "show_printer_ip": False,
        "show_serial": False,
    },
    "permissions": copy.deepcopy(DEFAULT_PERMISSIONS),
    "dashboard": {
        "default_tab": "dashboard",
        "poll_interval_ms": 2500,
        "auto_load_camera": True,
        "developer_mode": False,
    },
    "mobile": {
        "force_mobile_layout": False,
        "bottom_nav": True,
        "large_touch_controls": True,
    },
    "camera": {
        "prefer_direct": True,
        "proxy_fallback": True,
        "auto_wake": True,
    },
    "safety": {
        "enable_controls_by_default": False,
        "enable_dangerous_by_default": False,
        "confirm_cancel": True,
        "confirm_start_print": True,
        "confirm_delete_file": True,
        "confirm_history_delete": True,
        "confirm_temperature_change": False,
        "max_nozzle_temp": 320,
        "max_bed_temp": 120,
        "max_fan_percent": 100,
    },
    "theme": {
        "preset": "carbon",
        "glass": True,
        "animations": True,
        "accent": "blue",
    },
    "layout": {
        "tabs": [],
    },
    "developer": {
        "show_raw_console_payloads": False,
    },
}


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(low, min(high, n))


class AppConfigStore:
    """Persistent app-wide settings for cc2-dash.

    Printer pairing data stays in ConfigStore/printers.json. This store is for
    dashboard behavior, mobile layout, theme, camera behavior, safety limits,
    guest visibility, and auth/session policy.
    """

    def __init__(self, path: Path = DEFAULT_APP_CONFIG_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_APP_CONFIG)
        self.load()

    def load(self) -> Dict[str, Any]:
        with self.lock:
            if not self.path.exists():
                self._data = copy.deepcopy(DEFAULT_APP_CONFIG)
                self.ensure_session_secret(save=False)
                self.save()
                return self.as_dict()
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("app config root must be an object")
                self._data = _deep_merge(DEFAULT_APP_CONFIG, raw)
                self._normalize_in_place()
                if not self._data.get("auth", {}).get("session_secret"):
                    self.ensure_session_secret(save=False)
                    self.save()
            except Exception:
                backup = self.path.with_suffix(self.path.suffix + f".corrupt-{int(time.time())}")
                try:
                    self.path.replace(backup)
                except Exception:
                    pass
                self._data = copy.deepcopy(DEFAULT_APP_CONFIG)
                self.ensure_session_secret(save=False)
                self.save()
            return self.as_dict()

    def _normalize_in_place(self) -> None:
        d = self._data
        d["config_version"] = 2
        d.setdefault("server", {})["port"] = _clamp_int(d.get("server", {}).get("port"), 8088, 1, 65535)
        d.setdefault("dashboard", {})["poll_interval_ms"] = _clamp_int(d.get("dashboard", {}).get("poll_interval_ms"), 2500, 750, 60000)
        auth = d.setdefault("auth", {})
        auth["session_timeout_minutes"] = _clamp_int(auth.get("session_timeout_minutes"), 720, 5, 10080)
        auth["max_failed_attempts"] = _clamp_int(auth.get("max_failed_attempts"), 8, 2, 50)
        auth["lockout_minutes"] = _clamp_int(auth.get("lockout_minutes"), 10, 1, 1440)
        safety = d.setdefault("safety", {})
        safety["max_nozzle_temp"] = _clamp_int(safety.get("max_nozzle_temp"), 320, 0, 450)
        safety["max_bed_temp"] = _clamp_int(safety.get("max_bed_temp"), 120, 0, 160)
        safety["max_fan_percent"] = _clamp_int(safety.get("max_fan_percent"), 100, 0, 100)
        tabs = d.setdefault("layout", {}).get("tabs")
        if not isinstance(tabs, list):
            d["layout"]["tabs"] = []
        origins = d.setdefault("server", {}).get("allowed_origins")
        if not isinstance(origins, list):
            d["server"]["allowed_origins"] = []
        # Always merge permission tables forward so new permissions appear after upgrades.
        current_perms = d.get("permissions") if isinstance(d.get("permissions"), dict) else {}
        d["permissions"] = _deep_merge(DEFAULT_PERMISSIONS, current_perms)

    def ensure_session_secret(self, *, save: bool = True) -> str:
        with self.lock:
            auth = self._data.setdefault("auth", {})
            secret = str(auth.get("session_secret") or "")
            if not secret:
                secret = secrets.token_urlsafe(48)
                auth["session_secret"] = secret
                if save:
                    self.save()
            return secret

    def save(self) -> None:
        with self.lock:
            self._normalize_in_place()
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)

    def as_dict(self) -> Dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self._data)

    def public_dict(self, *, include_secret: bool = False) -> Dict[str, Any]:
        data = self.as_dict()
        if not include_secret:
            data.get("auth", {}).pop("session_secret", None)
        return data

    def patch(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("settings patch must be an object")
        with self.lock:
            # The session secret is generated/rotated by the server, not patched from the UI.
            if isinstance(patch.get("auth"), dict):
                patch = copy.deepcopy(patch)
                patch["auth"].pop("session_secret", None)
            self._data = _deep_merge(self._data, patch)
            self.save()
            return self.public_dict()

    def reset(self) -> Dict[str, Any]:
        with self.lock:
            old_secret = self._data.get("auth", {}).get("session_secret")
            self._data = copy.deepcopy(DEFAULT_APP_CONFIG)
            if old_secret:
                self._data.setdefault("auth", {})["session_secret"] = old_secret
            else:
                self.ensure_session_secret(save=False)
            self.save()
            return self.public_dict()

    def section(self, name: str) -> Dict[str, Any]:
        data = self.as_dict().get(name, {})
        return data if isinstance(data, dict) else {}

    @property
    def safety(self) -> Dict[str, Any]:
        return self.section("safety")
