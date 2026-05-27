from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from typing import Deque

from .config import DATA_DIR

_LOGS: Deque[dict] = deque(maxlen=750)
_LOG_PATH = DATA_DIR / "logs" / "system.jsonl"


def _safe_level(level: str) -> str:
    value = str(level or "info").upper()
    if value == "WARNING":
        value = "WARN"
    return value


def log(level: str, message: str, source: str = "app", **extra) -> None:
    row = {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "iso": datetime.now().isoformat(timespec="seconds"),
        "level": _safe_level(level),
        "source": str(source or "app"),
        "message": str(message),
        "extra": extra or {},
    }
    _LOGS.appendleft(row)
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Logging must never break printer control. Disk can fill up; vibes can not.
        pass


def _matches(row: dict, source: str | None = None, level: str | None = None, q: str | None = None) -> bool:
    if source and source != "all" and str(row.get("source") or "") != source:
        return False
    if level and level != "all" and str(row.get("level") or "").upper() != _safe_level(level):
        return False
    if q:
        hay = json.dumps(row, ensure_ascii=False, default=str).lower()
        if q.lower() not in hay:
            return False
    return True


def _read_persisted(limit: int = 500) -> list[dict]:
    if not _LOG_PATH.exists():
        return []
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(limit, 2000)):]
    except Exception:
        return []
    rows: list[dict] = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except Exception:
            continue
    return rows


def get_logs(limit: int = 120, source: str | None = None, level: str | None = None, q: str | None = None) -> list[dict]:
    limit = max(1, min(int(limit or 120), 1000))
    # Merge memory first, then persisted tail, de-duping by iso/source/message.
    seen = set()
    out: list[dict] = []
    for row in list(_LOGS) + _read_persisted(limit * 3):
        key = (row.get("iso"), row.get("source"), row.get("level"), row.get("message"))
        if key in seen:
            continue
        seen.add(key)
        if _matches(row, source=source, level=level, q=q):
            out.append(row)
            if len(out) >= limit:
                break
    return out


def log_sources() -> list[str]:
    sources = {str(r.get("source") or "app") for r in list(_LOGS) + _read_persisted(500)}
    return sorted(sources)
