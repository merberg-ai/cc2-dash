from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

DEFAULT_USERS_PATH = Path(os.environ.get("CC2_DASH_USERS", "config/users.json"))
HASH_SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 390_000


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("ascii"))


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{HASH_SCHEME}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_s, salt_s, digest_s = encoded.split("$", 3)
        if scheme != HASH_SCHEME:
            return False
        iterations = int(iterations_s)
        salt = _unb64(salt_s)
        expected = _unb64(digest_s)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def normalize_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("Username is required")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-@")
    if any(ch not in allowed for ch in username):
        raise ValueError("Username can only contain letters, numbers, dot, dash, underscore, or @")
    if len(username) > 96:
        raise ValueError("Username is too long")
    return username


@dataclass
class UserRecord:
    username: str
    role: str = "viewer"
    enabled: bool = True
    password_hash: str = ""
    display_name: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    last_login_at: Optional[float] = None

    def public(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("password_hash", None)
        data["password_set"] = bool(self.password_hash)
        return data


class AuthStore:
    """Tiny local user database stored in config/users.json.

    Passwords are salted PBKDF2-SHA256 hashes. This avoids plaintext passwords
    and keeps cc2-dash cloud-free with no extra auth service required.
    """

    def __init__(self, path: Path = DEFAULT_USERS_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self._users: Dict[str, UserRecord] = {}
        self.load()

    def load(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._users = {}
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                rows = raw.get("users", {}) if isinstance(raw, dict) else {}
                users: Dict[str, UserRecord] = {}
                if isinstance(rows, dict):
                    iterator = rows.items()
                elif isinstance(rows, list):
                    iterator = [(r.get("username"), r) for r in rows if isinstance(r, dict)]
                else:
                    iterator = []
                for key, value in iterator:
                    if not isinstance(value, dict):
                        continue
                    username = normalize_username(value.get("username") or key or "")
                    record = UserRecord(
                        username=username,
                        role=str(value.get("role") or "viewer"),
                        enabled=bool(value.get("enabled", True)),
                        password_hash=str(value.get("password_hash") or ""),
                        display_name=str(value.get("display_name") or ""),
                        created_at=float(value.get("created_at") or time.time()),
                        updated_at=float(value.get("updated_at") or time.time()),
                        last_login_at=value.get("last_login_at"),
                    )
                    users[username] = record
                self._users = users
            except Exception:
                backup = self.path.with_suffix(self.path.suffix + f".corrupt-{int(time.time())}")
                try:
                    self.path.replace(backup)
                except Exception:
                    pass
                self._users = {}

    def save(self) -> None:
        with self.lock:
            payload = {
                "config_version": 1,
                "users": {username: asdict(user) for username, user in sorted(self._users.items())},
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)

    def list_users(self) -> list[Dict[str, Any]]:
        with self.lock:
            return [u.public() for u in sorted(self._users.values(), key=lambda u: u.username)]

    def get(self, username: str) -> Optional[UserRecord]:
        with self.lock:
            try:
                return self._users.get(normalize_username(username))
            except ValueError:
                return None

    def has_admin(self) -> bool:
        with self.lock:
            return any(u.enabled and u.role == "admin" for u in self._users.values())

    def create_user(self, username: str, password: str, *, role: str = "viewer", enabled: bool = True, display_name: str = "") -> UserRecord:
        username = normalize_username(username)
        role = role if role in {"viewer", "operator", "admin"} else "viewer"
        now = time.time()
        with self.lock:
            if username in self._users:
                raise ValueError("User already exists")
            record = UserRecord(
                username=username,
                role=role,
                enabled=enabled,
                password_hash=hash_password(password),
                display_name=display_name.strip(),
                created_at=now,
                updated_at=now,
            )
            self._users[username] = record
            self.save()
            return record

    def update_user(self, username: str, *, password: Optional[str] = None, role: Optional[str] = None, enabled: Optional[bool] = None, display_name: Optional[str] = None) -> UserRecord:
        username = normalize_username(username)
        with self.lock:
            record = self._users.get(username)
            if not record:
                raise KeyError(username)
            if password:
                record.password_hash = hash_password(password)
            if role is not None:
                if role not in {"viewer", "operator", "admin"}:
                    raise ValueError("Invalid role")
                record.role = role
            if enabled is not None:
                record.enabled = bool(enabled)
            if display_name is not None:
                record.display_name = display_name.strip()
            record.updated_at = time.time()
            self.save()
            return record

    def delete_user(self, username: str) -> bool:
        username = normalize_username(username)
        with self.lock:
            if username not in self._users:
                return False
            del self._users[username]
            self.save()
            return True

    def verify_login(self, username: str, password: str) -> Optional[UserRecord]:
        record = self.get(username)
        if not record or not record.enabled or not record.password_hash:
            return None
        if not verify_password(password, record.password_hash):
            return None
        with self.lock:
            record.last_login_at = time.time()
            record.updated_at = time.time()
            self.save()
        return record
