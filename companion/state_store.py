"""Transactional, owner-scoped SQLite persistence for local companion state.

The database is local application state, not encrypted storage. Callers must keep
it inside the owner's private workspace and protect backups separately.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any


class CompanionStateStore:
    SCHEMA_VERSION = 1
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
            if row is None or row["version"] != self.SCHEMA_VERSION:
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

    def import_legacy_json(self, kind: str, source: str | Path) -> int:
        """Import a legacy JSON array once; never delete or rewrite the source."""
        self._check_kind(kind)
        source_path = Path(source).expanduser()
        source_name = f"{kind}:{source_path.name}"
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
                    datetime.fromisoformat(item["due_at"])
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
