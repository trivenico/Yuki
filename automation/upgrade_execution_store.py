"""Durable admission journal for isolated upgrade execution requests.

This is a control-plane persistence layer only. It never launches a runner,
claims isolation, or treats an uploaded evidence envelope as verified.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from automation.upgrade_execution_contracts import ExecutionEvidence, ExecutionRequest
from automation.upgrade_evidence_verifier import Ed25519EvidenceVerifier, EvidenceVerificationError


class UpgradeExecutionStoreError(RuntimeError):
    """Base error for upgrade execution persistence."""


class UpgradeExecutionConflict(UpgradeExecutionStoreError):
    """Raised when an idempotency key or compare-and-swap version conflicts."""


class UpgradeExecutionTransitionError(UpgradeExecutionStoreError):
    """Raised when a lifecycle transition is not permitted."""


_STATES = frozenset({
    "queued", "running", "evidence_pending", "verifying", "verified_pass", "verified_fail", "timed_out", "cancelled", "rejected",
})
_TRANSITIONS = {
    "queued": frozenset({"running", "cancelled", "rejected"}),
    "running": frozenset({"evidence_pending", "timed_out", "cancelled", "rejected"}),
    "evidence_pending": frozenset({"verifying", "timed_out", "cancelled", "rejected"}),
    "verifying": frozenset({"verified_pass", "verified_fail", "rejected"}),
    "verified_pass": frozenset(),
    "verified_fail": frozenset(),
    "timed_out": frozenset(),
    "cancelled": frozenset(),
    "rejected": frozenset(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UpgradeExecutionRecord:
    execution_id: str
    idempotency_key: str
    request_digest: str
    request_json: str
    state: str
    version: int
    created_at: str
    updated_at: str
    evidence_json: str | None


class UpgradeExecutionStore:
    """SQLite journal with immutable request snapshots and CAS lifecycle updates."""

    def __init__(self, database_path: str | Path) -> None:
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path_required")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS upgrade_executions (
                    execution_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    evidence_json TEXT,
                    evidence_digest TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS upgrade_execution_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(execution_id, sequence),
                    FOREIGN KEY(execution_id) REFERENCES upgrade_executions(execution_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS upgrade_execution_leases (
                    execution_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    lease_token TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(execution_id) REFERENCES upgrade_executions(execution_id)
                )
            """)
            # Enforce audit and snapshot immutability in SQLite itself, not
            # merely through the Python API. These triggers also protect
            # against accidental maintenance scripts bypassing the store.
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS upgrade_events_no_update
                BEFORE UPDATE ON upgrade_execution_events
                BEGIN
                    SELECT RAISE(ABORT, 'upgrade_event_is_append_only');
                END;

                CREATE TRIGGER IF NOT EXISTS upgrade_events_no_delete
                BEFORE DELETE ON upgrade_execution_events
                BEGIN
                    SELECT RAISE(ABORT, 'upgrade_event_is_append_only');
                END;

                CREATE TRIGGER IF NOT EXISTS upgrade_executions_no_delete
                BEFORE DELETE ON upgrade_executions
                BEGIN
                    SELECT RAISE(ABORT, 'upgrade_execution_is_retained');
                END;

                CREATE TRIGGER IF NOT EXISTS upgrade_request_snapshot_immutable
                BEFORE UPDATE OF execution_id, idempotency_key, request_digest,
                                 request_json, created_at
                ON upgrade_executions
                BEGIN
                    SELECT RAISE(ABORT, 'upgrade_request_snapshot_is_immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS upgrade_evidence_write_once
                BEFORE UPDATE OF evidence_json, evidence_digest
                ON upgrade_executions
                WHEN OLD.evidence_json IS NOT NULL
                     OR NEW.evidence_json IS NULL
                     OR NEW.evidence_digest IS NULL
                BEGIN
                    SELECT RAISE(ABORT, 'upgrade_evidence_is_write_once');
                END;
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> UpgradeExecutionRecord:
        return UpgradeExecutionRecord(
            execution_id=row["execution_id"],
            idempotency_key=row["idempotency_key"],
            request_digest=row["request_digest"],
            request_json=row["request_json"],
            state=row["state"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            evidence_json=row["evidence_json"],
        )

    def create(self, request: ExecutionRequest) -> UpgradeExecutionRecord:
        if not isinstance(request, ExecutionRequest):
            raise TypeError("request_must_be_ExecutionRequest")
        payload = json.dumps(request.to_dict(), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        now = _now()
        with self._transaction() as db:
            existing = db.execute(
                "SELECT * FROM upgrade_executions WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request.request_digest:
                    raise UpgradeExecutionConflict("idempotency_payload_mismatch")
                return self._record(existing)
            collision = db.execute(
                "SELECT 1 FROM upgrade_executions WHERE execution_id=?",
                (request.execution_id,),
            ).fetchone()
            if collision is not None:
                raise UpgradeExecutionConflict("execution_id_already_exists")
            db.execute("""
                INSERT INTO upgrade_executions
                (execution_id, idempotency_key, request_digest, request_json,
                 state, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', 0, ?, ?)
            """, (request.execution_id, request.idempotency_key,
                  request.request_digest, payload, now, now))
            db.execute("""
                INSERT INTO upgrade_execution_events
                (execution_id, sequence, from_state, to_state, actor_id, reason_code, created_at)
                VALUES (?, 0, NULL, 'queued', ?, 'admitted', ?)
            """, (request.execution_id, request.requesting_principal, now))
            row = db.execute(
                "SELECT * FROM upgrade_executions WHERE execution_id=?",
                (request.execution_id,),
            ).fetchone()
            return self._record(row)

    def get(self, execution_id: str) -> UpgradeExecutionRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM upgrade_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def transition(
        self, execution_id: str, *, expected_version: int,
        to_state: str, actor_id: str, reason_code: str,
    ) -> UpgradeExecutionRecord:
        if to_state not in _STATES:
            raise UpgradeExecutionTransitionError("unknown_state")
        if to_state in {"running", "evidence_pending", "verifying", "verified_pass", "verified_fail"}:
            raise UpgradeExecutionTransitionError("privileged_state_requires_verifier")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("actor_id_invalid")
        if not isinstance(reason_code, str) or not reason_code.strip() or len(reason_code) > 100:
            raise ValueError("reason_code_invalid")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM upgrade_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError("execution_not_found")
            if row["version"] != expected_version:
                raise UpgradeExecutionConflict("stale_execution_version")
            if to_state not in _TRANSITIONS[row["state"]]:
                raise UpgradeExecutionTransitionError("invalid_execution_transition")
            now = _now()
            next_version = expected_version + 1
            changed = db.execute("""
                UPDATE upgrade_executions
                SET state=?, version=?, updated_at=?
                WHERE execution_id=? AND version=?
            """, (to_state, next_version, now, execution_id, expected_version)).rowcount
            if changed != 1:
                raise UpgradeExecutionConflict("stale_execution_version")
            db.execute("""
                INSERT INTO upgrade_execution_events
                (execution_id, sequence, from_state, to_state, actor_id, reason_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (execution_id, next_version, row["state"], to_state,
                  actor_id, reason_code, now))
            updated = db.execute(
                "SELECT * FROM upgrade_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            return self._record(updated)

    def record_evidence(
        self, execution_id: str, *, expected_version: int,
        evidence: ExecutionEvidence,
    ) -> UpgradeExecutionRecord:
        """Persist an untrusted envelope after checking its request binding."""
        if not isinstance(evidence, ExecutionEvidence):
            raise TypeError("evidence_must_be_ExecutionEvidence")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM upgrade_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError("execution_not_found")
            if row["version"] != expected_version:
                raise UpgradeExecutionConflict("stale_execution_version")
            if row["state"] != "evidence_pending":
                raise UpgradeExecutionTransitionError("evidence_requires_pending_state")
            request_data = json.loads(row["request_json"])
            request_data["test_targets"] = tuple(request_data["test_targets"])
            request = ExecutionRequest(**request_data)
            try:
                evidence.verify_binding(request)
            except ValueError as exc:
                raise UpgradeExecutionConflict("evidence_request_mismatch") from exc
            if row["evidence_json"] is not None:
                raise UpgradeExecutionConflict("evidence_already_recorded")
            evidence_json = json.dumps(evidence.signed_payload() | {"signature": evidence.signature},
                                       ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False)
            evidence_digest = evidence.payload_digest
            now = _now()
            changed = db.execute("""
                UPDATE upgrade_executions
                SET evidence_json=?, evidence_digest=?, version=version+1, updated_at=?
                WHERE execution_id=? AND version=? AND state='evidence_pending'
            """, (evidence_json, evidence_digest, now, execution_id, expected_version)).rowcount
            if changed != 1:
                raise UpgradeExecutionConflict("stale_execution_version")
            next_version = expected_version + 1
            db.execute("""
                INSERT INTO upgrade_execution_events
                (execution_id, sequence, from_state, to_state, actor_id, reason_code, created_at)
                VALUES (?, ?, 'evidence_pending', 'evidence_pending', 'runner', 'evidence_received_unverified', ?)
            """, (execution_id, next_version, now))
            updated = db.execute(
                "SELECT * FROM upgrade_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            return self._record(updated)


    def verify_and_finalize_evidence(
        self, execution_id: str, *, expected_version: int,
        verifier: Ed25519EvidenceVerifier, actor_id: str = "evidence-verifier",
        now: datetime | None = None,
    ) -> UpgradeExecutionRecord:
        """Authenticate stored evidence before entering a verified terminal state."""
        if not isinstance(verifier, Ed25519EvidenceVerifier):
            raise TypeError("verifier_must_be_Ed25519EvidenceVerifier")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM upgrade_executions WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError("execution_not_found")
            if row["version"] != expected_version:
                raise UpgradeExecutionConflict("stale_execution_version")
            if row["state"] != "evidence_pending" or row["evidence_json"] is None:
                raise UpgradeExecutionTransitionError("verified_evidence_required")
            evidence = ExecutionEvidence(**json.loads(row["evidence_json"]))
            request_data = json.loads(row["request_json"])
            request_data["test_targets"] = tuple(request_data["test_targets"])
            request = ExecutionRequest(**request_data)
            try:
                evidence.verify_binding(request)
            except ValueError as exc:
                raise UpgradeExecutionConflict("evidence_request_mismatch") from exc
            recorded_at = _now()
            verification_time = now or datetime.now(timezone.utc)
            next_version = expected_version + 1
            changed = db.execute("""
                UPDATE upgrade_executions SET state='verifying', version=?, updated_at=?
                WHERE execution_id=? AND version=? AND state='evidence_pending'
            """, (next_version, recorded_at, execution_id, expected_version)).rowcount
            if changed != 1:
                raise UpgradeExecutionConflict("stale_execution_version")
            db.execute("""
                INSERT INTO upgrade_execution_events
                (execution_id, sequence, from_state, to_state, actor_id, reason_code, created_at)
                VALUES (?, ?, 'evidence_pending', 'verifying', ?, 'signature_verification_started', ?)
            """, (execution_id, next_version, actor_id, recorded_at))
        try:
            verifier.verify(evidence, now=verification_time)
        except EvidenceVerificationError as exc:
            target_state = "rejected"
            reason = str(exc)[:100]
        except Exception:
            # Fail closed: an unexpected verifier failure must never leave a
            # request eligible for an automatic retry or verified promotion.
            target_state = "rejected"
            reason = "verification_internal_error"
        else:
            target_state = "verified_pass" if evidence.outcome == "passed" and evidence.exit_code == 0 else "verified_fail"
            reason = "signature_and_claims_verified"
        return self._finalize_verified_transition(
            execution_id, expected_version=next_version, to_state=target_state,
            actor_id=actor_id, reason_code=reason,
        )

    def _finalize_verified_transition(
        self, execution_id: str, *, expected_version: int, to_state: str,
        actor_id: str, reason_code: str,
    ) -> UpgradeExecutionRecord:
        """CAS transition reachable only after verifier completion."""
        if to_state not in {"verified_pass", "verified_fail", "rejected"}:
            raise UpgradeExecutionTransitionError("verification_target_invalid")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM upgrade_executions WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                raise KeyError("execution_not_found")
            if row["version"] != expected_version:
                raise UpgradeExecutionConflict("stale_execution_version")
            if row["state"] != "verifying":
                raise UpgradeExecutionTransitionError("verification_not_in_progress")
            now_recorded = _now()
            changed = db.execute("""
                UPDATE upgrade_executions SET state=?, version=version+1, updated_at=?
                WHERE execution_id=? AND version=? AND state="verifying"
            """, (to_state, now_recorded, execution_id, expected_version)).rowcount
            if changed != 1:
                raise UpgradeExecutionConflict("stale_execution_version")
            db.execute("""
                INSERT INTO upgrade_execution_events
                (execution_id, sequence, from_state, to_state, actor_id, reason_code, created_at)
                VALUES (?, ?, "verifying", ?, ?, ?, ?)
            """, (execution_id, expected_version + 1, actor_id, reason_code, now_recorded))
            updated = db.execute("SELECT * FROM upgrade_executions WHERE execution_id=?", (execution_id,)).fetchone()
            return self._record(updated)


    def claim_queued(
        self, execution_id: str, *, owner_id: str, lease_token: str,
        lease_seconds: int = 60, now: datetime | None = None,
    ) -> UpgradeExecutionRecord:
        """Atomically claim one queued execution and create its worker lease."""
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id_invalid")
        if not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 256:
            raise ValueError("lease_token_invalid")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds_out_of_range")
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("now_must_be_timezone_aware")
        expires = instant.astimezone(timezone.utc) + timedelta(seconds=lease_seconds)
        now_text = instant.astimezone(timezone.utc).isoformat()
        expires_text = expires.isoformat()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM upgrade_executions WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                raise KeyError("execution_not_found")
            if row["state"] != "queued":
                raise UpgradeExecutionConflict("execution_not_queued")
            version = row["version"] + 1
            changed = db.execute("""
                UPDATE upgrade_executions SET state='running', version=?, updated_at=?
                WHERE execution_id=? AND state='queued' AND version=?
            """, (version, now_text, execution_id, row["version"])).rowcount
            if changed != 1:
                raise UpgradeExecutionConflict("execution_claim_race")
            db.execute("""
                INSERT INTO upgrade_execution_leases(execution_id, owner_id, lease_token, expires_at)
                VALUES (?, ?, ?, ?)
            """, (execution_id, owner_id, lease_token, expires_text))
            db.execute("""
                INSERT INTO upgrade_execution_events
                (execution_id, sequence, from_state, to_state, actor_id, reason_code, created_at)
                VALUES (?, ?, 'queued', 'running', ?, 'worker_lease_claimed', ?)
            """, (execution_id, version, owner_id, now_text))
            return self._record(db.execute("SELECT * FROM upgrade_executions WHERE execution_id=?", (execution_id,)).fetchone())

    def renew_lease(
        self, execution_id: str, *, owner_id: str, lease_token: str,
        lease_seconds: int = 60, now: datetime | None = None,
    ) -> str:
        """Renew only the current owner's still-live lease; return its expiry."""
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds_out_of_range")
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("now_must_be_timezone_aware")
        current = instant.astimezone(timezone.utc)
        expiry = (current + timedelta(seconds=lease_seconds)).isoformat()
        with self._transaction() as db:
            lease = db.execute("SELECT * FROM upgrade_execution_leases WHERE execution_id=?", (execution_id,)).fetchone()
            if lease is None or lease["owner_id"] != owner_id or lease["lease_token"] != lease_token:
                raise UpgradeExecutionConflict("lease_owner_or_token_mismatch")
            if datetime.fromisoformat(lease["expires_at"]) <= current:
                raise UpgradeExecutionConflict("lease_expired")
            row = db.execute("SELECT state FROM upgrade_executions WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None or row["state"] != "running":
                raise UpgradeExecutionConflict("execution_not_running")
            db.execute("UPDATE upgrade_execution_leases SET expires_at=? WHERE execution_id=?", (expiry, execution_id))
        return expiry

    def reject_expired_lease(
        self, execution_id: str, *, now: datetime | None = None,
        actor_id: str = "lease-reaper",
    ) -> UpgradeExecutionRecord:
        """Fail closed on an expired running worker; never silently re-run it."""
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("now_must_be_timezone_aware")
        now_text = instant.astimezone(timezone.utc).isoformat()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM upgrade_executions WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                raise KeyError("execution_not_found")
            lease = db.execute("SELECT * FROM upgrade_execution_leases WHERE execution_id=?", (execution_id,)).fetchone()
            if row["state"] != "running" or lease is None:
                raise UpgradeExecutionTransitionError("live_running_lease_required")
            if datetime.fromisoformat(lease["expires_at"]) > instant.astimezone(timezone.utc):
                raise UpgradeExecutionConflict("lease_not_expired")
            version = row["version"] + 1
            db.execute("""
                UPDATE upgrade_executions SET state='rejected', version=?, updated_at=?
                WHERE execution_id=? AND state='running' AND version=?
            """, (version, now_text, execution_id, row["version"]))
            db.execute("""
                INSERT INTO upgrade_execution_events
                (execution_id, sequence, from_state, to_state, actor_id, reason_code, created_at)
                VALUES (?, ?, 'running', 'rejected', ?, 'worker_lease_expired', ?)
            """, (execution_id, version, actor_id, now_text))
            return self._record(db.execute("SELECT * FROM upgrade_executions WHERE execution_id=?", (execution_id,)).fetchone())

    def list_events(self, execution_id: str) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT sequence, from_state, to_state, actor_id, reason_code, created_at
                FROM upgrade_execution_events WHERE execution_id=? ORDER BY sequence
            """, (execution_id,)).fetchall()
        return [dict(row) for row in rows]
