"""PostgreSQL-backed shared, hash-chained authorization audit repository."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import math

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from core.authorization import AuthorizationDecision, AuthorizationRequest
from core.authorization_audit import (
    AuthorizationAuditAnchor, AuthorizationAuditCheckpoint, _ZERO_HASH,
)


class PostgresAuthorizationAuditLog:
    """Shared audit chain; every writer serializes through one transactional lock."""

    _LOCK_CLASS = 0x59554B49
    _LOCK_OBJECT = 0x41554454

    def __init__(
        self, database_url: str, *, min_pool_size: int = 1,
        max_pool_size: int = 10, pool_timeout: float = 10.0,
    ) -> None:
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("authorization_audit_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("authorization_audit_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not math.isfinite(pool_timeout) or not 1 <= pool_timeout <= 120:
            raise ValueError("authorization_audit_pool_timeout_invalid")
        self._pool = ConnectionPool(
            conninfo=database_url, min_size=min_pool_size, max_size=max_pool_size,
            timeout=float(pool_timeout), kwargs={"row_factory": dict_row}, open=True,
        )

    @staticmethod
    def _digest(value: str | None) -> str | None:
        if value is None:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_event(event: dict) -> str:
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _columns() -> tuple[str, ...]:
        return (
            "occurred_at", "principal_hash", "tenant_hash", "agent_hash", "action",
            "resource_hash", "task_hash", "delegation_hash", "approval_hash", "risk",
            "allowed", "decision_hash", "policy_revision_hash", "reason_code",
        )

    def check_ready(self) -> bool:
        with self._pool.connection() as conn:
            state = conn.execute(
                "SELECT last_event_id, head_hash FROM authorization_audit_state WHERE singleton=1"
            ).fetchone()
            conn.execute("SELECT event_id FROM authorization_audit_events LIMIT 0")
        if state is None:
            raise RuntimeError("authorization_audit_schema_not_migrated")
        return True

    def __call__(self, request: AuthorizationRequest, decision: AuthorizationDecision) -> int:
        if not isinstance(request, AuthorizationRequest) or not isinstance(decision, AuthorizationDecision):
            raise TypeError("typed_authorization_audit_event_required")
        principal = request.principal
        values = {
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "principal_hash": self._digest(principal.principal_id),
            "tenant_hash": self._digest(principal.tenant_id),
            "agent_hash": self._digest(principal.agent_id),
            "action": request.action,
            "resource_hash": self._digest(request.resource),
            "task_hash": self._digest(request.task_id),
            "delegation_hash": self._digest(request.delegation_id),
            "approval_hash": self._digest(request.approval_id),
            "risk": request.risk,
            "allowed": int(decision.allowed),
            "decision_hash": self._digest(decision.decision_id),
            "policy_revision_hash": self._digest(decision.policy_revision),
            "reason_code": decision.reason_code,
        }
        columns = self._columns()
        placeholders = ",".join(["%s"] * (len(columns) + 3))
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (self._LOCK_CLASS, self._LOCK_OBJECT))
                state = conn.execute(
                    "SELECT last_event_id, head_hash FROM authorization_audit_state WHERE singleton=1 FOR UPDATE"
                ).fetchone()
                if state is None:
                    raise RuntimeError("authorization_audit_schema_not_migrated")
                event_id = int(state["last_event_id"]) + 1
                previous_hash = state["head_hash"]
                event = dict(values, previous_hash=previous_hash)
                event_hash = self._hash_event(event)
                conn.execute(
                    "INSERT INTO authorization_audit_events (event_id," + ",".join(columns) +
                    ",previous_hash,event_hash) VALUES (" + placeholders + ")",
                    (event_id,) + tuple(values[name] for name in columns) + (previous_hash, event_hash),
                )
                conn.execute(
                    "UPDATE authorization_audit_state SET last_event_id=%s, head_hash=%s WHERE singleton=1",
                    (event_id, event_hash),
                )
        return event_id

    def _rows(self):
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT event_id," + ",".join(self._columns()) +
                ",previous_hash,event_hash FROM authorization_audit_events ORDER BY event_id"
            ).fetchall()

    def verify_chain(self) -> bool:
        previous_hash = _ZERO_HASH
        expected_id = 1
        for row in self._rows():
            if type(row["event_id"]) is not int or row["event_id"] != expected_id:
                return False
            values = {name: row[name] for name in self._columns()}
            if not hmac.compare_digest(row["previous_hash"], previous_hash):
                return False
            if not hmac.compare_digest(
                self._hash_event(dict(values, previous_hash=row["previous_hash"])),
                row["event_hash"],
            ):
                return False
            previous_hash = row["event_hash"]
            expected_id += 1
        with self._pool.connection() as conn:
            state = conn.execute(
                "SELECT last_event_id, head_hash FROM authorization_audit_state WHERE singleton=1"
            ).fetchone()
        return bool(state) and state["last_event_id"] == expected_id - 1 and hmac.compare_digest(state["head_hash"], previous_hash)

    def head_hash(self) -> str:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT head_hash FROM authorization_audit_state WHERE singleton=1").fetchone()
        if row is None:
            raise RuntimeError("authorization_audit_schema_not_migrated")
        return row["head_hash"]

    def checkpoint(self) -> AuthorizationAuditCheckpoint:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT last_event_id, head_hash FROM authorization_audit_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise RuntimeError("authorization_audit_schema_not_migrated")
        return AuthorizationAuditCheckpoint(int(row["last_event_id"]), row["head_hash"], datetime.now(timezone.utc).isoformat())

    def event_page(self, *, after_event_id: int = 0, limit: int = 500) -> tuple[dict, ...]:
        if type(after_event_id) is not int or after_event_id < 0 or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("authorization_audit_page_invalid")
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT event_id, occurred_at, principal_hash, reason_code, allowed "
                "FROM authorization_audit_events WHERE event_id>%s ORDER BY event_id LIMIT %s",
                (after_event_id, limit),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def verify_anchor(self, anchor: AuthorizationAuditAnchor, *, max_age_seconds: float | None = None, now: datetime | None = None) -> bool:
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        try:
            checkpoint = anchor.latest()
        except Exception:
            return False
        if not isinstance(checkpoint, AuthorizationAuditCheckpoint) or type(checkpoint.event_id) is not int or checkpoint.event_id < 0:
            return False
        if not isinstance(checkpoint.event_hash, str) or len(checkpoint.event_hash) != 64 or any(c not in "0123456789abcdef" for c in checkpoint.event_hash):
            return False
        if not isinstance(checkpoint.published_at, str) or not checkpoint.published_at or len(checkpoint.published_at) > 64:
            return False
        if max_age_seconds is not None:
            if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
                return False
            try:
                published = datetime.fromisoformat(checkpoint.published_at)
                current = now or datetime.now(timezone.utc)
                if published.tzinfo is None or current.tzinfo is None:
                    return False
                age = (current - published).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return False
            if age < -30 or age > max_age_seconds:
                return False
        if not self.verify_chain():
            return False
        if checkpoint.event_id == 0:
            return hmac.compare_digest(checkpoint.event_hash, _ZERO_HASH)
        with self._pool.connection() as conn:
            row = conn.execute("SELECT event_hash FROM authorization_audit_events WHERE event_id=%s", (checkpoint.event_id,)).fetchone()
        return bool(row) and hmac.compare_digest(row["event_hash"], checkpoint.event_hash)

    def publish_checkpoint(self, anchor: AuthorizationAuditAnchor) -> AuthorizationAuditCheckpoint:
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        latest = anchor.latest()
        current = self.checkpoint()
        if latest is not None:
            if not isinstance(latest, AuthorizationAuditCheckpoint):
                raise RuntimeError("authorization_audit_anchor_malformed")
            if latest.event_id > current.event_id:
                raise RuntimeError("authorization_audit_local_history_behind_anchor")
            if latest.event_id == current.event_id and not hmac.compare_digest(latest.event_hash, current.event_hash):
                raise RuntimeError("authorization_audit_anchor_head_mismatch")
            if not self.verify_anchor(anchor):
                raise RuntimeError("authorization_audit_anchor_integrity_mismatch")
        anchor.publish(current)
        published = anchor.latest()
        if not isinstance(published, AuthorizationAuditCheckpoint) or published.event_id != current.event_id or not hmac.compare_digest(published.event_hash, current.event_hash):
            raise RuntimeError("authorization_audit_anchor_publication_unconfirmed")
        return published

    def import_sqlite_history(self, source_path: str, anchor: AuthorizationAuditAnchor) -> int:
        """Import a verified SQLite chain into an empty PostgreSQL target.

        The source is opened read-only. All source rows are validated before a
        single PostgreSQL transaction inserts their original IDs and hashes.
        The external anchor must already attest to a prefix of that source.
        """
        import sqlite3
        from pathlib import Path
        from urllib.parse import quote

        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        try:
            path = Path(source_path).expanduser().resolve(strict=True)
            if not path.is_file():
                raise ValueError("authorization_audit_source_not_file")
            uri = "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5.0) as source:
                source.execute("PRAGMA query_only=ON")
                columns = self._columns()
                rows = source.execute(
                    "SELECT event_id," + ",".join(columns) +
                    ",previous_hash,event_hash FROM authorization_audit_events ORDER BY event_id"
                ).fetchall()
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise RuntimeError("authorization_audit_source_unreadable") from exc

        previous_hash = _ZERO_HASH
        verified_rows = []
        anchored_hash = _ZERO_HASH
        for expected_id, row in enumerate(rows, start=1):
            event_id = row[0]
            values = dict(zip(columns, row[1:1 + len(columns)]))
            stored_previous, stored_hash = row[-2:]
            if type(event_id) is not int or event_id != expected_id:
                raise RuntimeError("authorization_audit_source_event_sequence_invalid")
            if not isinstance(stored_previous, str) or not hmac.compare_digest(stored_previous, previous_hash):
                raise RuntimeError("authorization_audit_source_previous_hash_invalid")
            calculated = self._hash_event(dict(values, previous_hash=stored_previous))
            if not isinstance(stored_hash, str) or not hmac.compare_digest(calculated, stored_hash):
                raise RuntimeError("authorization_audit_source_event_hash_invalid")
            previous_hash = stored_hash
            verified_rows.append((event_id,) + tuple(values[name] for name in columns) + (stored_previous, stored_hash))

        try:
            checkpoint = anchor.latest()
        except Exception as exc:
            raise RuntimeError("authorization_audit_anchor_unavailable") from exc
        if (
            not isinstance(checkpoint, AuthorizationAuditCheckpoint)
            or type(checkpoint.event_id) is not int
            or checkpoint.event_id < 0
            or checkpoint.event_id > len(verified_rows)
            or not isinstance(checkpoint.event_hash, str)
            or len(checkpoint.event_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in checkpoint.event_hash)
            or not isinstance(checkpoint.published_at, str)
            or not checkpoint.published_at
        ):
            raise RuntimeError("authorization_audit_anchor_checkpoint_invalid")
        expected_anchor_hash = _ZERO_HASH if checkpoint.event_id == 0 else verified_rows[checkpoint.event_id - 1][-1]
        if not hmac.compare_digest(expected_anchor_hash, checkpoint.event_hash):
            raise RuntimeError("authorization_audit_source_does_not_match_anchor")

        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (self._LOCK_CLASS, self._LOCK_OBJECT))
                state = conn.execute(
                    "SELECT last_event_id, head_hash FROM authorization_audit_state WHERE singleton=1 FOR UPDATE"
                ).fetchone()
                count = conn.execute("SELECT count(*) AS n FROM authorization_audit_events").fetchone()["n"]
                if state is None or state["last_event_id"] != 0 or count != 0 or not hmac.compare_digest(state["head_hash"], _ZERO_HASH):
                    raise RuntimeError("authorization_audit_postgres_target_not_empty")
                if verified_rows:
                    names = ("event_id",) + columns + ("previous_hash", "event_hash")
                    placeholders = ",".join(["%s"] * len(names))
                    sql = "INSERT INTO authorization_audit_events (" + ",".join(names) + ") VALUES (" + placeholders + ")"
                    with conn.cursor() as cursor:
                        cursor.executemany(sql, verified_rows)
                conn.execute(
                    "UPDATE authorization_audit_state SET last_event_id=%s, head_hash=%s WHERE singleton=1",
                    (len(verified_rows), previous_hash),
                )
        if not self.verify_chain() or not self.verify_anchor(anchor):
            raise RuntimeError("authorization_audit_post_import_verification_failed")
        return len(verified_rows)

    def close(self) -> None:
        self._pool.close()
