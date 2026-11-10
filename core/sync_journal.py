"""Append-only event journal primitives for future local/cloud Yuki sync.

This module provides storage and idempotent replication primitives only. It does
not open network listeners, authenticate peers, or automatically sync data.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Iterable

_MAX_EVENT_BYTES = 64 * 1024
_MAX_EVENT_KIND = 80
_MAX_ORIGIN = 128
_KIND_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_ORIGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class SyncJournalError(RuntimeError):
    """Base class for sync journal errors."""


class InvalidSyncEvent(SyncJournalError, ValueError):
    """Raised when an event fails schema or size validation."""


@dataclass(frozen=True)
class SyncEvent:
    event_id: str
    origin_id: str
    sequence: int
    kind: str
    entity_id: str
    payload: dict[str, Any]
    created_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload_json(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise InvalidSyncEvent("Event payload must be a JSON object.")
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise InvalidSyncEvent("Event payload must be JSON serializable.") from exc
    if len(raw.encode("utf-8")) > _MAX_EVENT_BYTES:
        raise InvalidSyncEvent("Event payload exceeds the size limit.")
    return raw


def _validate_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise InvalidSyncEvent("Event must be an object.")
    event_id = event.get("event_id")
    origin_id = event.get("origin_id")
    sequence = event.get("sequence")
    kind = event.get("kind")
    entity_id = event.get("entity_id")
    created_at = event.get("created_at")
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 64:
        raise InvalidSyncEvent("Invalid event ID.")
    try:
        uuid.UUID(event_id)
    except (ValueError, AttributeError) as exc:
        raise InvalidSyncEvent("Event ID must be a UUID.") from exc
    if not isinstance(origin_id, str) or not _ORIGIN_RE.fullmatch(origin_id):
        raise InvalidSyncEvent("Invalid event origin.")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise InvalidSyncEvent("Event sequence must be a positive integer.")
    if not isinstance(kind, str) or not _KIND_RE.fullmatch(kind):
        raise InvalidSyncEvent("Invalid event kind.")
    if not isinstance(entity_id, str) or not 1 <= len(entity_id) <= 200 or "\x00" in entity_id:
        raise InvalidSyncEvent("Invalid entity ID.")
    if not isinstance(created_at, str) or not 1 <= len(created_at) <= 64:
        raise InvalidSyncEvent("Invalid event timestamp.")
    payload_json = _payload_json(event.get("payload"))
    normalized = {
        "event_id": event_id,
        "origin_id": origin_id,
        "sequence": sequence,
        "kind": kind,
        "entity_id": entity_id,
        "payload_json": payload_json,
        "created_at": created_at,
    }
    if len(json.dumps(normalized, separators=(",", ":")).encode("utf-8")) > _MAX_EVENT_BYTES + 1024:
        raise InvalidSyncEvent("Event exceeds the size limit.")
    return normalized


class SyncJournal:
    """SQLite append-only journal with idempotent remote event ingestion."""

    def __init__(self, database_path: str, origin_id: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if not isinstance(origin_id, str) or not _ORIGIN_RE.fullmatch(origin_id):
            raise ValueError("origin_id must be a stable safe identifier.")
        self.database_path = str(Path(database_path).expanduser())
        self.origin_id = origin_id
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS sync_events (
                    event_id TEXT PRIMARY KEY,
                    origin_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(origin_id, sequence)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS sync_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            row = db.execute("SELECT value FROM sync_meta WHERE key='origin_id'").fetchone()
            if row is None:
                db.execute("INSERT INTO sync_meta(key, value) VALUES('origin_id', ?)", (origin_id,))
            elif row["value"] != origin_id:
                raise SyncJournalError("Database origin ID does not match configured origin.")

    @contextmanager
    def _connect(self):
        """Commit successful units of work and roll back failures before closing."""
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _decode(row) -> SyncEvent:
        return SyncEvent(
            event_id=row["event_id"],
            origin_id=row["origin_id"],
            sequence=row["sequence"],
            kind=row["kind"],
            entity_id=row["entity_id"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )

    def append(self, kind: str, entity_id: str, payload: dict[str, Any]) -> SyncEvent:
        payload_json = _payload_json(payload)
        with self._connect() as db:
            # Serialize sequence allocation and insert so concurrent writers
            # cannot both observe the same maximum sequence.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS seq FROM sync_events WHERE origin_id=?",
                (self.origin_id,),
            ).fetchone()
            sequence = int(row["seq"]) + 1
            raw = {
                "event_id": str(uuid.uuid4()),
                "origin_id": self.origin_id,
                "sequence": sequence,
                "kind": kind,
                "entity_id": entity_id,
                "payload": json.loads(payload_json),
                "created_at": _utcnow(),
            }
            normalized = _validate_event(raw)
            db.execute(
                "INSERT INTO sync_events(event_id, origin_id, sequence, kind, entity_id, payload_json, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (normalized["event_id"], normalized["origin_id"], normalized["sequence"],
                 normalized["kind"], normalized["entity_id"], normalized["payload_json"],
                 normalized["created_at"]),
            )
            row = db.execute("SELECT * FROM sync_events WHERE event_id=?", (normalized["event_id"],)).fetchone()
            return self._decode(row)

    def export_after(self, cursors: dict[str, int] | None = None, *, limit: int = 500) -> list[SyncEvent]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000.")
        cursors = {} if cursors is None else cursors
        if not isinstance(cursors, dict) or any(
            not isinstance(k, str) or not _ORIGIN_RE.fullmatch(k)
            or isinstance(v, bool) or not isinstance(v, int) or v < 0
            for k, v in cursors.items()
        ):
            raise ValueError("cursors must map origin IDs to non-negative integer sequences.")
        with self._connect() as db:
            if cursors:
                known = list(cursors)
                clauses = ["origin_id NOT IN (" + ",".join("?" for _ in known) + ")"]
                params = list(known)
                for origin, sequence in cursors.items():
                    clauses.append("(origin_id=? AND sequence>?)")
                    params.extend((origin, sequence))
                query = (
                    "SELECT * FROM sync_events WHERE (" + " OR ".join(clauses) + ") "
                    "ORDER BY origin_id, sequence LIMIT ?"
                )
                params.append(limit)
                rows = db.execute(query, params).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM sync_events ORDER BY origin_id, sequence LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._decode(row) for row in rows]

    def ingest(self, events: Iterable[SyncEvent | dict[str, Any]], *, limit: int = 5000) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000.")
        accepted = []
        for i, item in enumerate(events):
            if i >= limit:
                raise InvalidSyncEvent("Batch exceeds the event count limit.")
            if isinstance(item, SyncEvent):
                item = {
                    "event_id": item.event_id, "origin_id": item.origin_id,
                    "sequence": item.sequence, "kind": item.kind, "entity_id": item.entity_id,
                    "payload": item.payload, "created_at": item.created_at,
                }
            accepted.append(_validate_event(item))
        inserted = 0
        with self._connect() as db:
            for item in accepted:
                existing = db.execute(
                    "SELECT origin_id, sequence, kind, entity_id, payload_json, created_at "
                    "FROM sync_events WHERE event_id=?", (item["event_id"],)
                ).fetchone()
                if existing:
                    comparable = tuple(existing[k] for k in
                        ("origin_id", "sequence", "kind", "entity_id", "payload_json", "created_at"))
                    incoming = tuple(item[k] for k in
                        ("origin_id", "sequence", "kind", "entity_id", "payload_json", "created_at"))
                    if comparable != incoming:
                        raise SyncJournalError("Conflicting content for an existing event ID.")
                    continue
                try:
                    db.execute(
                        "INSERT INTO sync_events(event_id, origin_id, sequence, kind, entity_id, payload_json, created_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (item["event_id"], item["origin_id"], item["sequence"], item["kind"],
                         item["entity_id"], item["payload_json"], item["created_at"]),
                    )
                    inserted += 1
                except sqlite3.IntegrityError as exc:
                    raise SyncJournalError("Conflicting origin sequence; refusing to overwrite existing events.") from exc
        return inserted

    def cursors(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT origin_id, MAX(sequence) AS seq FROM sync_events GROUP BY origin_id"
            ).fetchall()
        return {row["origin_id"]: int(row["seq"]) for row in rows}
