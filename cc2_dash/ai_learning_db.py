from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import DATA_DIR
from .logger import log

DB_PATH = DATA_DIR / "ai_learning.sqlite3"
SCHEMA_VERSION = "1"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open the lightweight AI learning SQLite database."""
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=3.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=3000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_database(db_path: Path | None = None) -> dict[str, Any]:
    """Create or migrate the AI learning database."""
    path = Path(db_path or DB_PATH)
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                printer_id TEXT NOT NULL,
                feedback_label TEXT NOT NULL,
                feedback_note TEXT,
                outcome TEXT,
                ai_was_warning INTEGER NOT NULL DEFAULT 0,
                user_says_failure INTEGER NOT NULL DEFAULT 0,
                file_name TEXT,
                print_stage TEXT,
                progress_percent REAL,
                risk_score REAL,
                severity REAL,
                confidence REAL,
                vision_state TEXT,
                dark_luma REAL,
                contrast REAL,
                edge_density REAL,
                edge_delta REAL,
                triggered_flags TEXT,
                suppression_match INTEGER DEFAULT 0,
                model_name TEXT,
                prompt_version TEXT,
                frame_path TEXT,
                raw_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_printer_created
                ON feedback_samples(printer_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_feedback_printer_outcome
                ON feedback_samples(printer_id, outcome);

            CREATE INDEX IF NOT EXISTS idx_feedback_file
                ON feedback_samples(printer_id, file_name);

            CREATE TABLE IF NOT EXISTS learning_profiles (
                printer_id TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                learning_mode TEXT NOT NULL DEFAULT 'suggest_only',
                confidence TEXT NOT NULL DEFAULT 'none',
                sample_count INTEGER DEFAULT 0,
                true_positive_count INTEGER DEFAULT 0,
                false_positive_count INTEGER DEFAULT 0,
                false_negative_count INTEGER DEFAULT 0,
                true_negative_count INTEGER DEFAULT 0,
                dark_luma_modifier REAL DEFAULT 0,
                edge_density_modifier REAL DEFAULT 0,
                required_bad_checks_modifier INTEGER DEFAULT 0,
                suggested_dark_luma_modifier REAL DEFAULT 0,
                suggested_edge_density_modifier REAL DEFAULT 0,
                suggested_required_bad_checks_modifier INTEGER DEFAULT 0,
                normal_luma_median REAL,
                normal_luma_p10 REAL,
                normal_luma_p90 REAL,
                normal_contrast_median REAL,
                normal_contrast_p10 REAL,
                normal_contrast_p90 REAL,
                normal_edge_density_median REAL,
                normal_edge_density_p90 REAL,
                normal_edge_density_p95 REAL,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                printer_id TEXT,
                event_type TEXT NOT NULL,
                level TEXT DEFAULT 'info',
                message TEXT,
                raw_json TEXT
            );
            """
        )
        current = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        current_version = current["value"] if current else None
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        if current_version != SCHEMA_VERSION:
            log_event(
                "sqlite_migration_complete",
                printer_id=None,
                message=f"AI learning SQLite schema v{SCHEMA_VERSION} ready.",
                conn=conn,
                raw={"path": str(path), "schema_version": SCHEMA_VERSION, "previous_schema_version": current_version},
            )
    return health(db_path=path)


def health(db_path: Path | None = None) -> dict[str, Any]:
    path = Path(db_path or DB_PATH)
    exists = path.exists()
    out: dict[str, Any] = {
        "ok": exists,
        "path": str(path),
        "exists": exists,
        "schema_version": None,
        "feedback_samples": 0,
        "profiles": 0,
        "events": 0,
    }
    if not exists:
        return out
    try:
        with connect(path) as conn:
            row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            out["schema_version"] = row["value"] if row else None
            out["feedback_samples"] = int(conn.execute("SELECT COUNT(*) AS c FROM feedback_samples").fetchone()["c"])
            out["profiles"] = int(conn.execute("SELECT COUNT(*) AS c FROM learning_profiles").fetchone()["c"])
            out["events"] = int(conn.execute("SELECT COUNT(*) AS c FROM learning_events").fetchone()["c"])
            out["ok"] = True
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)
    return out


def row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {k: row[k] for k in row.keys()}


def log_event(
    event_type: str,
    printer_id: str | None = None,
    level: str = "info",
    message: str | None = None,
    raw: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    payload = (
        _now_iso(),
        printer_id,
        str(event_type or "event"),
        str(level or "info"),
        message or "",
        json.dumps(raw or {}, ensure_ascii=False, default=str),
    )

    def _insert(c: sqlite3.Connection) -> None:
        c.execute(
            """
            INSERT INTO learning_events(created_at, printer_id, event_type, level, message, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            payload,
        )

    if conn is not None:
        _insert(conn)
        return
    with connect() as own:
        _insert(own)


def insert_feedback_sample(sample: dict[str, Any]) -> dict[str, Any]:
    """Insert one structured feedback sample, skipping obvious duplicates."""
    ensure_database()
    created_at = str(sample.get("created_at") or _now_iso())
    printer_id = str(sample.get("printer_id") or "unknown")
    label = str(sample.get("feedback_label") or sample.get("label") or "unknown")
    frame_path = str(sample.get("frame_path") or "")
    raw_json = sample.get("raw_json")
    if not isinstance(raw_json, str):
        raw_json = json.dumps(raw_json if raw_json is not None else sample, ensure_ascii=False, default=str)
    triggered_flags = sample.get("triggered_flags")
    if not isinstance(triggered_flags, str):
        triggered_flags = json.dumps(triggered_flags or [], ensure_ascii=False, default=str)

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id FROM feedback_samples
            WHERE created_at=? AND printer_id=? AND feedback_label=? AND COALESCE(frame_path,'')=?
            LIMIT 1
            """,
            (created_at, printer_id, label, frame_path),
        ).fetchone()
        if existing:
            return {"inserted": False, "duplicate": True, "id": int(existing["id"])}
        cur = conn.execute(
            """
            INSERT INTO feedback_samples(
                created_at, printer_id, feedback_label, feedback_note, outcome,
                ai_was_warning, user_says_failure, file_name, print_stage, progress_percent,
                risk_score, severity, confidence, vision_state,
                dark_luma, contrast, edge_density, edge_delta, triggered_flags,
                suppression_match, model_name, prompt_version, frame_path, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                printer_id,
                label,
                sample.get("feedback_note") or sample.get("note") or "",
                sample.get("outcome"),
                1 if sample.get("ai_was_warning") else 0,
                1 if sample.get("user_says_failure") else 0,
                sample.get("file_name"),
                sample.get("print_stage"),
                sample.get("progress_percent"),
                sample.get("risk_score"),
                sample.get("severity"),
                sample.get("confidence"),
                sample.get("vision_state"),
                sample.get("dark_luma"),
                sample.get("contrast"),
                sample.get("edge_density"),
                sample.get("edge_delta"),
                triggered_flags,
                1 if sample.get("suppression_match") else 0,
                sample.get("model_name"),
                sample.get("prompt_version"),
                frame_path or None,
                raw_json,
            ),
        )
        sample_id = int(cur.lastrowid)
        log_event(
            "feedback_sample_imported",
            printer_id=printer_id,
            message=f"Feedback sample {sample_id} inserted.",
            conn=conn,
            raw={"id": sample_id, "label": label, "outcome": sample.get("outcome")},
        )
        return {"inserted": True, "duplicate": False, "id": sample_id}



def update_feedback_reason(
    sample_id: int | None,
    printer_id: str,
    reason: str,
    reason_key: str = "",
    label: str = "",
    feedback_timestamp: float | None = None,
) -> dict[str, Any]:
    """Attach an optional user-selected reason/note to an existing feedback sample.

    Feedback labels are saved immediately so the dashboard never blocks on a second
    prompt. This helper lets the optional reason chip update the structured SQLite
    row afterward while preserving the original raw payload for audit/debugging.
    """
    ensure_database()
    reason = str(reason or "").strip()[:240]
    reason_key = str(reason_key or "").strip()[:80]
    label = str(label or "").strip()
    if not reason:
        return {"ok": False, "updated": False, "error": "empty_reason"}

    with connect() as conn:
        row = None
        if sample_id:
            row = conn.execute(
                "SELECT * FROM feedback_samples WHERE id=? AND printer_id=? LIMIT 1",
                (int(sample_id), printer_id),
            ).fetchone()
        if row is None and feedback_timestamp is not None:
            # Fallback for older clients: match by ISO timestamp derived from the
            # JSONL epoch plus printer/label, newest first. Timestamps are usually
            # exact because sample_from_feedback_row uses the same epoch value.
            try:
                created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(feedback_timestamp)))
            except Exception:
                created_at = ""
            if created_at:
                row = conn.execute(
                    """
                    SELECT * FROM feedback_samples
                    WHERE printer_id=? AND created_at=? AND (?='' OR feedback_label=?)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (printer_id, created_at, label, label),
                ).fetchone()
        if row is None:
            return {"ok": False, "updated": False, "error": "sample_not_found"}

        raw = row["raw_json"]
        try:
            raw_obj = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except Exception:
            raw_obj = {"raw_json_parse_error": True, "raw_json_previous": raw}
        if not isinstance(raw_obj, dict):
            raw_obj = {"raw_json_previous": raw_obj}

        raw_obj["note"] = reason
        raw_obj["reason"] = reason
        raw_obj["reason_key"] = reason_key
        raw_obj["reason_updated_at"] = _now_iso()
        snapshot = raw_obj.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
            raw_obj["snapshot"] = snapshot
        snapshot["note"] = reason
        snapshot["reason"] = reason
        snapshot["reason_key"] = reason_key
        ctx = snapshot.get("client_context")
        if not isinstance(ctx, dict):
            ctx = {}
            snapshot["client_context"] = ctx
        ctx["feedback_reason"] = reason
        ctx["feedback_reason_key"] = reason_key

        conn.execute(
            "UPDATE feedback_samples SET feedback_note=?, raw_json=? WHERE id=?",
            (reason, json.dumps(raw_obj, ensure_ascii=False, default=str), int(row["id"])),
        )
        log_event(
            "feedback_reason_updated",
            printer_id=printer_id,
            message=f"Feedback sample {int(row['id'])} reason saved: {reason}",
            conn=conn,
            raw={"id": int(row["id"]), "label": row["feedback_label"], "reason_key": reason_key, "reason": reason},
        )
        return {"ok": True, "updated": True, "id": int(row["id"]), "reason": reason, "reason_key": reason_key}


def fetch_samples(printer_id: str, limit: int = 500) -> list[dict[str, Any]]:
    ensure_database()
    limit = max(1, min(int(limit or 500), 5000))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM feedback_samples
            WHERE printer_id=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (printer_id, limit),
        ).fetchall()
    return [row_to_dict(r) or {} for r in rows]


def fetch_recent_samples(printer_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    ensure_database()
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    with connect() as conn:
        if printer_id:
            rows = conn.execute(
                """
                SELECT * FROM feedback_samples
                WHERE printer_id=?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (printer_id, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM feedback_samples
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
    return [row_to_dict(r) or {} for r in rows]


def list_printer_ids() -> list[str]:
    ensure_database()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT printer_id FROM feedback_samples
            UNION
            SELECT printer_id FROM learning_profiles
            ORDER BY printer_id
            """
        ).fetchall()
    return [str(r["printer_id"]) for r in rows if r["printer_id"]]


def upsert_profile(profile: dict[str, Any]) -> dict[str, Any]:
    ensure_database()
    raw = profile.get("raw_json")
    if not isinstance(raw, str):
        raw = json.dumps(raw if raw is not None else profile, ensure_ascii=False, default=str)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO learning_profiles(
                printer_id, updated_at, learning_mode, confidence,
                sample_count, true_positive_count, false_positive_count, false_negative_count, true_negative_count,
                dark_luma_modifier, edge_density_modifier, required_bad_checks_modifier,
                suggested_dark_luma_modifier, suggested_edge_density_modifier, suggested_required_bad_checks_modifier,
                normal_luma_median, normal_luma_p10, normal_luma_p90,
                normal_contrast_median, normal_contrast_p10, normal_contrast_p90,
                normal_edge_density_median, normal_edge_density_p90, normal_edge_density_p95,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(printer_id) DO UPDATE SET
                updated_at=excluded.updated_at,
                learning_mode=excluded.learning_mode,
                confidence=excluded.confidence,
                sample_count=excluded.sample_count,
                true_positive_count=excluded.true_positive_count,
                false_positive_count=excluded.false_positive_count,
                false_negative_count=excluded.false_negative_count,
                true_negative_count=excluded.true_negative_count,
                dark_luma_modifier=excluded.dark_luma_modifier,
                edge_density_modifier=excluded.edge_density_modifier,
                required_bad_checks_modifier=excluded.required_bad_checks_modifier,
                suggested_dark_luma_modifier=excluded.suggested_dark_luma_modifier,
                suggested_edge_density_modifier=excluded.suggested_edge_density_modifier,
                suggested_required_bad_checks_modifier=excluded.suggested_required_bad_checks_modifier,
                normal_luma_median=excluded.normal_luma_median,
                normal_luma_p10=excluded.normal_luma_p10,
                normal_luma_p90=excluded.normal_luma_p90,
                normal_contrast_median=excluded.normal_contrast_median,
                normal_contrast_p10=excluded.normal_contrast_p10,
                normal_contrast_p90=excluded.normal_contrast_p90,
                normal_edge_density_median=excluded.normal_edge_density_median,
                normal_edge_density_p90=excluded.normal_edge_density_p90,
                normal_edge_density_p95=excluded.normal_edge_density_p95,
                raw_json=excluded.raw_json
            """,
            (
                profile.get("printer_id"),
                profile.get("updated_at") or _now_iso(),
                profile.get("learning_mode") or "suggest_only",
                profile.get("confidence") or "none",
                int(profile.get("sample_count") or 0),
                int(profile.get("true_positive_count") or 0),
                int(profile.get("false_positive_count") or 0),
                int(profile.get("false_negative_count") or 0),
                int(profile.get("true_negative_count") or 0),
                float(profile.get("dark_luma_modifier") or 0),
                float(profile.get("edge_density_modifier") or 0),
                int(profile.get("required_bad_checks_modifier") or 0),
                float(profile.get("suggested_dark_luma_modifier") or 0),
                float(profile.get("suggested_edge_density_modifier") or 0),
                int(profile.get("suggested_required_bad_checks_modifier") or 0),
                profile.get("normal_luma_median"),
                profile.get("normal_luma_p10"),
                profile.get("normal_luma_p90"),
                profile.get("normal_contrast_median"),
                profile.get("normal_contrast_p10"),
                profile.get("normal_contrast_p90"),
                profile.get("normal_edge_density_median"),
                profile.get("normal_edge_density_p90"),
                profile.get("normal_edge_density_p95"),
                raw,
            ),
        )
        log_event(
            "profile_rebuilt",
            printer_id=str(profile.get("printer_id") or ""),
            message=f"Learning profile rebuilt with {int(profile.get('sample_count') or 0)} samples.",
            conn=conn,
            raw={"confidence": profile.get("confidence"), "suggestions": profile.get("raw_json", {}).get("reasons") if isinstance(profile.get("raw_json"), dict) else None},
        )
    return profile


def get_profile(printer_id: str) -> dict[str, Any] | None:
    ensure_database()
    with connect() as conn:
        row = conn.execute("SELECT * FROM learning_profiles WHERE printer_id=?", (printer_id,)).fetchone()
    return row_to_dict(row)


def get_profiles() -> list[dict[str, Any]]:
    ensure_database()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM learning_profiles ORDER BY updated_at DESC").fetchall()
    return [row_to_dict(r) or {} for r in rows]


def reset_profile(printer_id: str | None = None, delete_samples: bool = False) -> dict[str, Any]:
    ensure_database()
    with connect() as conn:
        if printer_id:
            conn.execute("DELETE FROM learning_profiles WHERE printer_id=?", (printer_id,))
            conn.execute("DELETE FROM learning_events WHERE printer_id=?", (printer_id,))
            if delete_samples:
                conn.execute("DELETE FROM feedback_samples WHERE printer_id=?", (printer_id,))
            log_event(
                "learning_reset",
                printer_id=printer_id,
                message="Learning profile/events reset." + (" Samples deleted." if delete_samples else " Samples kept."),
                conn=conn,
                raw={"delete_samples": delete_samples},
            )
        else:
            conn.execute("DELETE FROM learning_profiles")
            conn.execute("DELETE FROM learning_events")
            if delete_samples:
                conn.execute("DELETE FROM feedback_samples")
            log_event(
                "learning_reset",
                printer_id=None,
                message="All learning profiles/events reset." + (" Samples deleted." if delete_samples else " Samples kept."),
                conn=conn,
                raw={"delete_samples": delete_samples},
            )
    return {"ok": True, "printer_id": printer_id, "delete_samples": delete_samples}
