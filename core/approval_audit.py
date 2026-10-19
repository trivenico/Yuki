"""SQLite persistent audit log for approval events (metadata only)."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class SQLiteApprovalAuditLog:
    def __init__(self, database_path: str):
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approval_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                pending_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

    def record_event(self, event_type: str, pending_id: str, actor: str, session_id: str):
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.database_path) as db:
            db.execute(
                "INSERT INTO approval_audit (event_type, pending_id, actor, session_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (event_type, pending_id, actor, session_id, now),
            )

    def list_events(self):
        with sqlite3.connect(self.database_path) as db:
            db.row_factory = sqlite3.Row
            cursor = db.execute("SELECT id, event_type, pending_id, actor, session_id, created_at FROM approval_audit ORDER BY id DESC")
            return [dict(row) for row in cursor.fetchall()]
