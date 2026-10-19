"""SQLite append-only approval audit log with bounded, privacy-preserving metadata."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3


def _identity_fingerprint(value: str) -> str:
    # Domain separation prevents the same identifier hash being reused as an
    # action fingerprint in another log.
    return "sha256:" + hashlib.sha256(("yuki-audit-identity:v1:" + value).encode("utf-8")).hexdigest()


class SQLiteApprovalAuditLog:
    """Durable audit events; raw actor/session identifiers are never persisted."""

    def __init__(self, database_path: str):
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approval_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                pending_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            # One-time compatibility migration: redact identifiers written by
            # earlier versions before normal operation continues.
            rows = db.execute(
                "SELECT id, actor, session_id FROM approval_audit"
            ).fetchall()
            for row_id, actor, session_id in rows:
                actor_hash = actor if actor.startswith("sha256:") else _identity_fingerprint(actor)
                session_hash = session_id if session_id.startswith("sha256:") else _identity_fingerprint(session_id)
                if actor_hash != actor or session_hash != session_id:
                    db.execute(
                        "UPDATE approval_audit SET actor = ?, session_id = ? WHERE id = ?",
                        (actor_hash, session_hash, row_id),
                    )
            db.execute("""CREATE TRIGGER IF NOT EXISTS approval_audit_no_update
                BEFORE UPDATE ON approval_audit
                BEGIN SELECT RAISE(ABORT, 'approval audit is append-only'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS approval_audit_no_delete
                BEFORE DELETE ON approval_audit
                BEGIN SELECT RAISE(ABORT, 'approval audit is append-only'); END""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = FULL")
        return db

    def record_event(self, event_type: str, pending_id: str, actor: str, session_id: str):
        for label, value, maximum in (
            ("event_type", event_type, 64),
            ("pending_id", pending_id, 128),
            ("actor", actor, 256),
            ("session_id", session_id, 256),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO approval_audit (event_type, pending_id, actor, session_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (event_type, pending_id, _identity_fingerprint(actor), _identity_fingerprint(session_id), now),
            )

    def list_events(self):
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            cursor = db.execute("SELECT id, event_type, pending_id, actor, session_id, created_at FROM approval_audit ORDER BY id DESC")
            return [dict(row) for row in cursor.fetchall()]
