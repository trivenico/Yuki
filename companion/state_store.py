"""Transactional, owner-scoped SQLite persistence for local companion state.

The database is local application state, not encrypted storage. Callers must keep
it inside the owner's private workspace and protect backups separately.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
from pathlib import Path
import sqlite3
from typing import Any


class CompanionStateStore:
    SCHEMA_VERSION = 2
    MAX_RECORDS = {"memory": 2000, "identity": 100, "reminder": 1000}

    def __init__(self, database_path: str | Path, *, owner_id: str):
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be non-empty text of at most 200 characters.")
        raw_path = Path(database_path).expanduser()
        if raw_path.exists() and raw_path.is_symlink():
            raise ValueError("companion database path must not be a symlink.")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.parent.is_symlink():
            raise ValueError("companion database directory must not be a symlink.")
        self.database_path = raw_path
        self.owner_id = owner_id.strip()
        self._initialize()
        try:
            if os.name == "posix":
                os.chmod(self.database_path, 0o600)
        except OSError as exc:
            raise RuntimeError("Could not secure companion database permissions.") from exc

    @contextmanager
    def _connect(self):
        if self.database_path.is_symlink():
            raise RuntimeError("companion database path must not be a symlink.")
        db = sqlite3.connect(str(self.database_path), timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    def _initialize(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS companion_schema (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    version INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO companion_schema(singleton, version) VALUES(1, 1);
                CREATE TABLE IF NOT EXISTS companion_records (
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('memory','identity','reminder')),
                    record_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    PRIMARY KEY(owner_id, kind, record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_companion_records_due
                    ON companion_records(owner_id, kind, delivered_at, created_at);
                CREATE TABLE IF NOT EXISTS companion_migrations (
                    owner_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    migrated_at TEXT NOT NULL,
                    imported_count INTEGER NOT NULL,
                    PRIMARY KEY(owner_id, source_name)
                );
            """)
            row = db.execute("SELECT version FROM companion_schema WHERE singleton=1").fetchone()
            if row is None or row["version"] > self.SCHEMA_VERSION:
                raise RuntimeError("Unsupported companion state schema version.")
            if row["version"] == 1:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS companion_outbox (
                        owner_id TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        reminder_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending','leased','delivered','failed')),
                        created_at TEXT NOT NULL,
                        available_at TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                        lease_token_hash TEXT,
                        lease_expires_at TEXT,
                        delivered_at TEXT,
                        last_error TEXT,
                        PRIMARY KEY(owner_id, event_id),
                        UNIQUE(owner_id, reminder_id)
                    )
                """)
                db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_companion_outbox_claim
                    ON companion_outbox(owner_id, status, available_at, lease_expires_at)
                """)
                db.execute("UPDATE companion_schema SET version=? WHERE singleton=1", (self.SCHEMA_VERSION,))
                db.commit()
            elif row["version"] != self.SCHEMA_VERSION:
                raise RuntimeError("Unsupported companion state schema version.")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _check_kind(kind: str):
        if not isinstance(kind, str) or kind not in CompanionStateStore.MAX_RECORDS:
            raise ValueError("unsupported companion record kind.")

    @staticmethod
    def _decode(row):
        item = json.loads(row["payload"])
        if not isinstance(item, dict):
            raise RuntimeError("Companion state record is corrupt.")
        return item

    def list_records(self, kind: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        self._check_kind(kind)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= self.MAX_RECORDS[kind]):
            raise ValueError("limit is outside the supported range.")
        order = "DESC" if kind == "memory" and limit is not None else "ASC"
        query = f"SELECT payload,delivered_at FROM companion_records WHERE owner_id=? AND kind=? ORDER BY created_at {order}, record_id {order}"
        params: tuple[Any, ...] = (self.owner_id, kind)
        if limit is not None:
            query += " LIMIT ?"
            params += (limit,)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        if kind == "memory" and limit is not None:
            rows.reverse()
        items = [self._decode(row) for row in rows]
        if kind == "reminder":
            for item, row in zip(items, rows):
                item["delivered"] = row["delivered_at"] is not None
        return items

    def add_record(self, kind: str, item: dict[str, Any]) -> dict[str, Any]:
        self._check_kind(kind)
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("record must contain a non-empty id.")
        if "created_at" in item and not isinstance(item["created_at"], str):
            raise ValueError("created_at must be text.")
        payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(payload) > 16_384:
            raise ValueError("record exceeds the storage limit.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) AS n FROM companion_records WHERE owner_id=? AND kind=?",
                (self.owner_id, kind),
            ).fetchone()["n"]
            existing = db.execute(
                "SELECT 1 FROM companion_records WHERE owner_id=? AND kind=? AND record_id=?",
                (self.owner_id, kind, item["id"]),
            ).fetchone()
            if kind == "identity":
                for row in db.execute(
                    "SELECT payload FROM companion_records WHERE owner_id=? AND kind='identity'",
                    (self.owner_id,),
                ).fetchall():
                    saved = json.loads(row["payload"])
                    if saved.get("name", "").casefold() == item.get("name", "").casefold():
                        db.rollback()
                        raise ValueError("Identity already exists.")
            if existing is None and count >= self.MAX_RECORDS[kind]:
                db.rollback()
                raise ValueError(f"{kind} store is full.")
            db.execute(
                """INSERT INTO companion_records(owner_id,kind,record_id,payload,created_at,delivered_at)
                   VALUES(?,?,?,?,?,NULL)""",
                (self.owner_id, kind, item["id"], payload, item.get("created_at", self._now())),
            )
            db.commit()
        return dict(item)

    def delete_record(self, kind: str, record_id: str) -> bool:
        self._check_kind(kind)
        if not isinstance(record_id, str) or not record_id.strip():
            return False
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM companion_records WHERE owner_id=? AND kind=? AND record_id=?",
                (self.owner_id, kind, record_id),
            )
            return cursor.rowcount == 1

    def clear_records(self, kind: str) -> int:
        self._check_kind(kind)
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM companion_records WHERE owner_id=? AND kind=?",
                (self.owner_id, kind),
            )
            return cursor.rowcount

    def claim_due_reminders(self, now: datetime) -> list[dict[str, Any]]:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        cutoff = now.astimezone(timezone.utc).isoformat()
        claimed: list[dict[str, Any]] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT record_id,payload FROM companion_records
                   WHERE owner_id=? AND kind='reminder' AND delivered_at IS NULL
                   ORDER BY created_at,record_id""",
                (self.owner_id,),
            ).fetchall()
            for row in rows:
                item = json.loads(row["payload"])
                due_at = datetime.fromisoformat(item["due_at"])
                if due_at.tzinfo is None or due_at.utcoffset() is None:
                    raise RuntimeError("Stored reminder timestamp must be timezone-aware.")
                if due_at <= now:
                    claimed.append(item)
                    db.execute(
                        """UPDATE companion_records SET delivered_at=?
                           WHERE owner_id=? AND kind='reminder' AND record_id=? AND delivered_at IS NULL""",
                        (cutoff, self.owner_id, row["record_id"]),
                    )
            db.commit()
        return claimed

    @staticmethod
    def _aware_utc(value: datetime, field: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware.")
        return value.astimezone(timezone.utc)

    def enqueue_due_reminders(self, now: datetime) -> int:
        """Durably enqueue due reminders without treating enqueue as delivery."""
        cutoff = self._aware_utc(now, "now").isoformat()
        inserted = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT record_id,payload,created_at FROM companion_records
                   WHERE owner_id=? AND kind='reminder' AND delivered_at IS NULL
                   ORDER BY created_at,record_id""", (self.owner_id,)
            ).fetchall()
            for row in rows:
                item = json.loads(row["payload"])
                due_at = self._aware_utc(datetime.fromisoformat(item["due_at"]), "reminder due_at")
                if due_at > datetime.fromisoformat(cutoff):
                    continue
                event_id = f"reminder:{row['record_id']}"
                cursor = db.execute(
                    """INSERT OR IGNORE INTO companion_outbox
                       (owner_id,event_id,reminder_id,payload,status,created_at,available_at)
                       VALUES(?,?,?,?,'pending',?,?)""",
                    (self.owner_id, event_id, row["record_id"], row["payload"], cutoff, cutoff),
                )
                inserted += cursor.rowcount
            db.commit()
        return inserted

    def claim_outbox(
        self, now: datetime, *, lease_seconds: int = 60, limit: int = 20,
        max_attempts: int = 8,
    ) -> list[dict[str, Any]]:
        """Lease pending events; callers must use event_id as provider idempotency key."""
        current = self._aware_utc(now, "now")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 5 and 3600.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20.")
        expires = (current + timedelta(seconds=lease_seconds)).isoformat()
        claimed = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE companion_outbox SET status='failed',
                   last_error=COALESCE(last_error,'lease expired after maximum attempts'),
                   lease_token_hash=NULL,lease_expires_at=NULL
                   WHERE owner_id=? AND status='leased' AND lease_expires_at<=? AND attempts>=?""",
                (self.owner_id, current.isoformat(), max_attempts),
            )
            rows = db.execute(
                """SELECT event_id,payload,attempts FROM companion_outbox
                   WHERE owner_id=? AND attempts<? AND (
                     (status='pending' AND available_at<=?) OR
                     (status='leased' AND lease_expires_at<=?)
                   ) ORDER BY available_at,event_id LIMIT ?""",
                (self.owner_id, max_attempts, current.isoformat(), current.isoformat(), limit),
            ).fetchall()
            for row in rows:
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                db.execute(
                    """UPDATE companion_outbox SET status='leased',attempts=attempts+1,
                       lease_token_hash=?,lease_expires_at=?
                       WHERE owner_id=? AND event_id=?""",
                    (token_hash, expires, self.owner_id, row["event_id"]),
                )
                claimed.append({
                    "event_id": row["event_id"],
                    "idempotency_key": row["event_id"],
                    "payload": json.loads(row["payload"]),
                    "attempt": row["attempts"] + 1,
                    "lease_token": token,
                    "lease_expires_at": expires,
                })
            db.commit()
        return claimed

    def complete_outbox(self, event_id: str, lease_token: str, now: datetime) -> bool:
        current = self._aware_utc(now, "now")
        if not isinstance(event_id, str) or not event_id or not isinstance(lease_token, str) or not lease_token:
            return False
        token_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        delivered_at = current.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT reminder_id FROM companion_outbox
                   WHERE owner_id=? AND event_id=? AND status='leased'
                     AND lease_token_hash=? AND lease_expires_at>?""",
                (self.owner_id, event_id, token_hash, delivered_at),
            ).fetchone()
            if row is None:
                db.rollback()
                return False
            db.execute(
                """UPDATE companion_outbox SET status='delivered',delivered_at=?,
                   lease_token_hash=NULL,lease_expires_at=NULL,last_error=NULL
                   WHERE owner_id=? AND event_id=?""",
                (delivered_at, self.owner_id, event_id),
            )
            db.execute(
                """UPDATE companion_records SET delivered_at=?
                   WHERE owner_id=? AND kind='reminder' AND record_id=? AND delivered_at IS NULL""",
                (delivered_at, self.owner_id, row["reminder_id"]),
            )
            db.commit()
        return True

    def fail_outbox(
        self, event_id: str, lease_token: str, now: datetime, *,
        error: str, retry_after_seconds: int = 60, max_attempts: int = 8,
    ) -> bool:
        current = self._aware_utc(now, "now")
        if not isinstance(event_id, str) or not event_id or not isinstance(lease_token, str) or not lease_token:
            return False
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be non-empty text.")
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 86400:
            raise ValueError("retry_after_seconds must be between 1 and 86400.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20.")
        token_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT attempts FROM companion_outbox
                   WHERE owner_id=? AND event_id=? AND status='leased'
                     AND lease_token_hash=? AND lease_expires_at>?""",
                (self.owner_id, event_id, token_hash, current.isoformat()),
            ).fetchone()
            if row is None:
                db.rollback()
                return False
            terminal = row["attempts"] >= max_attempts
            next_status = "failed" if terminal else "pending"
            available = current.isoformat() if terminal else (
                current + timedelta(seconds=retry_after_seconds)
            ).isoformat()
            db.execute(
                """UPDATE companion_outbox SET status=?,available_at=?,last_error=?,
                   lease_token_hash=NULL,lease_expires_at=NULL
                   WHERE owner_id=? AND event_id=?""",
                (next_status, available, error.strip()[:1000], self.owner_id, event_id),
            )
            db.commit()
        return True

    def list_outbox(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id,reminder_id,status,created_at,available_at,attempts,
                   lease_expires_at,delivered_at,last_error FROM companion_outbox
                   WHERE owner_id=? ORDER BY created_at,event_id LIMIT ?""",
                (self.owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def import_legacy_json(self, kind: str, source: str | Path) -> int:
        """Import a legacy JSON array once; never delete or rewrite the source."""
        self._check_kind(kind)
        source_path = Path(source).expanduser()
        source_name = f"{kind}:{source_path.resolve()}"
        with self._connect() as db:
            marker = db.execute(
                "SELECT 1 FROM companion_migrations WHERE owner_id=? AND source_name=?",
                (self.owner_id, source_name),
            ).fetchone()
            if marker is not None:
                return 0
        try:
            raw = json.loads(source_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = []
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Legacy companion state is unreadable; source was preserved.") from exc
        if not isinstance(raw, list) or len(raw) > self.MAX_RECORDS[kind]:
            raise RuntimeError("Legacy companion state has an invalid structure or exceeds limits.")
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise RuntimeError("Legacy companion state contains an invalid record.")
            if "created_at" in item and not isinstance(item["created_at"], str):
                raise RuntimeError("Legacy companion timestamp is invalid.")
            if kind == "memory" and not isinstance(item.get("text"), str):
                raise RuntimeError("Legacy memory record is invalid.")
            if kind == "identity" and (not isinstance(item.get("name"), str) or item.get("consent") is not True):
                raise RuntimeError("Legacy identity record is invalid.")
            if kind == "reminder":
                try:
                    due_at = datetime.fromisoformat(item["due_at"])
                    if due_at.tzinfo is None or due_at.utcoffset() is None:
                        raise ValueError("reminder timestamp must be timezone-aware")
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError("Legacy reminder record is invalid.") from exc
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            already = db.execute(
                "SELECT 1 FROM companion_migrations WHERE owner_id=? AND source_name=?",
                (self.owner_id, source_name),
            ).fetchone()
            if already is not None:
                db.rollback()
                return 0
            for item in raw:
                payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                delivered = self._now() if kind == "reminder" and item.get("delivered") is True else None
                db.execute(
                    """INSERT OR IGNORE INTO companion_records
                       (owner_id,kind,record_id,payload,created_at,delivered_at)
                       VALUES(?,?,?,?,?,?)""",
                    (self.owner_id, kind, item["id"], payload,
                     item.get("created_at", item.get("due_at", self._now())), delivered),
                )
            db.execute(
                "INSERT INTO companion_migrations(owner_id,source_name,migrated_at,imported_count) VALUES(?,?,?,?)",
                (self.owner_id, source_name, self._now(), len(raw)),
            )
            db.commit()
        return len(raw)
