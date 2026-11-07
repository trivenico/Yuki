"""Durable cross-process reviewer worker registry and concurrency slots.

This registry is intentionally separate from task execution. It stores only
worker identity, liveness leases, and slot reservations; it never stores
reviewer credentials, task payloads, or signing keys. Expired workers are
reported as recovery candidates, not automatically allowed to replay tasks.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Callable
import uuid


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ReviewerWorkerRegistryError(RuntimeError):
    """Base registry error."""


class ReviewerWorkerRegistryConflict(ReviewerWorkerRegistryError):
    """A worker lease or slot reservation is stale or conflicting."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewerWorkerRegistration:
    worker_id: str
    instance_id: str
    lease_token: str
    lease_expires_at: str


@dataclass(frozen=True)
class ReviewerWorkerStatus:
    worker_id: str
    instance_id: str
    state: str
    registered_at: str
    heartbeat_at: str
    lease_expires_at: str
    active_slots: int


class ReviewerWorkerRegistry:
    """SQLite-backed worker liveness and global per-specialist slot authority.

    authorize(actor_id, action, reviewer_agent_id) must resolve a trusted
    principal and return literal True. Every operation is authorization gated.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        authorize: Callable[[str, str, str], bool],
    ) -> None:
        if not callable(authorize):
            raise TypeError("trusted_worker_registry_authorizer_required")
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path_required")
        self.authorize = authorize
        Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_worker_instances (
                instance_id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL,
                token_digest TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('running','stopping','stopped','expired')),
                registered_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                lease_expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_reviewer_workers_live
                ON reviewer_worker_instances(worker_id,state,lease_expires_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_worker_slots (
                slot_id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL,
                instance_id TEXT NOT NULL REFERENCES reviewer_worker_instances(instance_id),
                acquired_at TEXT NOT NULL,
                lease_expires_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_reviewer_worker_slots_live
                ON reviewer_worker_slots(worker_id,lease_expires_at)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _id(value: str, code: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError(code)
        return value

    @staticmethod
    def _ttl(value: int, code: str) -> int:
        if type(value) is not int or not 10 <= value <= 900:
            raise ValueError(code)
        return value

    def _allowed(self, actor: str, action: str, worker: str) -> bool:
        try:
            return self.authorize(actor, action, worker) is True
        except Exception:
            return False

    def register(
        self, *, actor_id: str, worker_id: str, instance_id: str | None = None,
        lease_seconds: int = 60,
    ) -> ReviewerWorkerRegistration:
        actor = self._id(actor_id, "invalid_worker_registry_actor")
        worker = self._id(worker_id, "invalid_reviewer_worker_id")
        instance = self._id(instance_id or str(uuid.uuid4()), "invalid_worker_instance_id")
        ttl = self._ttl(lease_seconds, "invalid_worker_lease_ttl")
        if not self._allowed(actor, "review:worker:register", worker):
            raise PermissionError("reviewer worker registration not authorized")
        now = _now()
        token = secrets.token_urlsafe(32)
        expiry = _stamp(now + timedelta(seconds=ttl))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT state,lease_expires_at FROM reviewer_worker_instances WHERE instance_id=?",
                (instance,),
            ).fetchone()
            if old and old["state"] in ("running", "stopping") and old["lease_expires_at"] > _stamp(now):
                raise ReviewerWorkerRegistryConflict("worker_instance_already_live")
            db.execute("""INSERT INTO reviewer_worker_instances
                (instance_id,worker_id,token_digest,state,registered_at,heartbeat_at,
                 lease_expires_at,updated_at) VALUES (?,?,?,'running',?,?,?,?)
                ON CONFLICT(instance_id) DO UPDATE SET worker_id=excluded.worker_id,
                 token_digest=excluded.token_digest,state='running',
                 registered_at=excluded.registered_at,heartbeat_at=excluded.heartbeat_at,
                 lease_expires_at=excluded.lease_expires_at,updated_at=excluded.updated_at""",
                (instance, worker, _digest(token), _stamp(now), _stamp(now), expiry, _stamp(now)))
            return ReviewerWorkerRegistration(worker, instance, token, expiry)

    def heartbeat(
        self, *, actor_id: str, worker_id: str, instance_id: str,
        lease_token: str, lease_seconds: int = 60, state: str = "running",
    ) -> str:
        actor = self._id(actor_id, "invalid_worker_registry_actor")
        worker = self._id(worker_id, "invalid_reviewer_worker_id")
        instance = self._id(instance_id, "invalid_worker_instance_id")
        ttl = self._ttl(lease_seconds, "invalid_worker_lease_ttl")
        if state not in ("running", "stopping"):
            raise ValueError("invalid_worker_heartbeat_state")
        if not isinstance(lease_token, str) or not 20 <= len(lease_token) <= 256:
            raise ValueError("invalid_worker_lease_token")
        if not self._allowed(actor, "review:worker:heartbeat", worker):
            raise PermissionError("reviewer worker heartbeat not authorized")
        now = _now()
        expiry = _stamp(now + timedelta(seconds=ttl))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_worker_instances WHERE instance_id=?",
                             (instance,)).fetchone()
            if (row is None or row["worker_id"] != worker
                or row["token_digest"] != _digest(lease_token)
                or row["state"] not in ("running", "stopping")
                or row["lease_expires_at"] <= _stamp(now)):
                raise ReviewerWorkerRegistryConflict("worker_lease_invalid_or_expired")
            db.execute("""UPDATE reviewer_worker_instances SET state=?,heartbeat_at=?,
                lease_expires_at=?,updated_at=? WHERE instance_id=?""",
                (state, _stamp(now), expiry, _stamp(now), instance))
            return expiry

    def claim_slot(
        self, *, actor_id: str, worker_id: str, instance_id: str,
        lease_token: str, max_concurrency: int, slot_seconds: int = 60,
    ) -> str:
        actor = self._id(actor_id, "invalid_worker_registry_actor")
        worker = self._id(worker_id, "invalid_reviewer_worker_id")
        instance = self._id(instance_id, "invalid_worker_instance_id")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 32:
            raise ValueError("invalid_global_reviewer_concurrency")
        ttl = self._ttl(slot_seconds, "invalid_worker_slot_ttl")
        if not isinstance(lease_token, str) or not 20 <= len(lease_token) <= 256:
            raise ValueError("invalid_worker_lease_token")
        if not self._allowed(actor, "review:worker:slot:claim", worker):
            raise PermissionError("reviewer worker slot claim not authorized")
        now = _now()
        stamp = _stamp(now)
        expiry = _stamp(now + timedelta(seconds=ttl))
        slot = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT * FROM reviewer_worker_instances WHERE instance_id=?",
                               (instance,)).fetchone()
            if (owner is None or owner["worker_id"] != worker
                or owner["token_digest"] != _digest(lease_token)
                or owner["state"] != "running" or owner["lease_expires_at"] <= stamp):
                raise ReviewerWorkerRegistryConflict("worker_not_live_for_slot_claim")
            db.execute("DELETE FROM reviewer_worker_slots WHERE lease_expires_at<=?", (stamp,))
            count = db.execute("""SELECT COUNT(*) AS n FROM reviewer_worker_slots
                WHERE worker_id=? AND lease_expires_at>?""", (worker, stamp)).fetchone()["n"]
            if count >= max_concurrency:
                raise ReviewerWorkerRegistryConflict("reviewer_global_concurrency_limit_reached")
            db.execute("""INSERT INTO reviewer_worker_slots
                (slot_id,worker_id,instance_id,acquired_at,lease_expires_at)
                VALUES (?,?,?,?,?)""", (slot, worker, instance, stamp, expiry))
            return slot

    def release_slot(
        self, *, actor_id: str, worker_id: str, instance_id: str,
        lease_token: str, slot_id: str,
    ) -> None:
        actor = self._id(actor_id, "invalid_worker_registry_actor")
        worker = self._id(worker_id, "invalid_reviewer_worker_id")
        instance = self._id(instance_id, "invalid_worker_instance_id")
        slot = self._id(slot_id, "invalid_worker_slot_id")
        if not isinstance(lease_token, str) or not 20 <= len(lease_token) <= 256:
            raise ValueError("invalid_worker_lease_token")
        if not self._allowed(actor, "review:worker:slot:release", worker):
            raise PermissionError("reviewer worker slot release not authorized")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT token_digest FROM reviewer_worker_instances WHERE instance_id=? AND worker_id=?",
                               (instance, worker)).fetchone()
            if owner is None or owner["token_digest"] != _digest(lease_token):
                raise ReviewerWorkerRegistryConflict("worker_lease_invalid")
            db.execute("DELETE FROM reviewer_worker_slots WHERE slot_id=? AND instance_id=?",
                       (slot, instance))

    def stop(
        self, *, actor_id: str, worker_id: str, instance_id: str,
        lease_token: str,
    ) -> None:
        actor = self._id(actor_id, "invalid_worker_registry_actor")
        worker = self._id(worker_id, "invalid_reviewer_worker_id")
        instance = self._id(instance_id, "invalid_worker_instance_id")
        if not isinstance(lease_token, str) or not 20 <= len(lease_token) <= 256:
            raise ValueError("invalid_worker_lease_token")
        if not self._allowed(actor, "review:worker:stop", worker):
            raise PermissionError("reviewer worker stop not authorized")
        now = _stamp(_now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT token_digest FROM reviewer_worker_instances WHERE instance_id=? AND worker_id=?",
                             (instance, worker)).fetchone()
            if row is None or row["token_digest"] != _digest(lease_token):
                raise ReviewerWorkerRegistryConflict("worker_lease_invalid")
            db.execute("""UPDATE reviewer_worker_instances SET state='stopped',
                heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE instance_id=?""",
                (now, now, now, instance))
            db.execute("DELETE FROM reviewer_worker_slots WHERE instance_id=?", (instance,))

    def expire_stale(self, *, actor_id: str, worker_id: str) -> int:
        actor = self._id(actor_id, "invalid_worker_registry_actor")
        worker = self._id(worker_id, "invalid_reviewer_worker_id")
        if not self._allowed(actor, "review:worker:recover", worker):
            raise PermissionError("reviewer worker recovery not authorized")
        now = _stamp(_now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE reviewer_worker_instances SET state='expired',
                updated_at=? WHERE worker_id=? AND state IN ('running','stopping')
                AND lease_expires_at<=?""", (now, worker, now)).rowcount
            db.execute("""DELETE FROM reviewer_worker_slots WHERE worker_id=?
                AND lease_expires_at<=?""", (worker, now))
            return changed

    def list_workers(self, *, actor_id: str, worker_id: str) -> tuple[ReviewerWorkerStatus, ...]:
        actor = self._id(actor_id, "invalid_worker_registry_actor")
        worker = self._id(worker_id, "invalid_reviewer_worker_id")
        if not self._allowed(actor, "review:worker:inspect", worker):
            raise PermissionError("reviewer worker inspection not authorized")
        now = _stamp(_now())
        with self._connect() as db:
            rows = db.execute("""SELECT w.*,
                (SELECT COUNT(*) FROM reviewer_worker_slots s
                 WHERE s.instance_id=w.instance_id AND s.lease_expires_at>?) AS active_slots
                FROM reviewer_worker_instances w WHERE w.worker_id=?
                ORDER BY w.registered_at,w.instance_id""", (now, worker)).fetchall()
        return tuple(ReviewerWorkerStatus(
            worker_id=row["worker_id"], instance_id=row["instance_id"],
            state=("expired" if row["state"] in ("running", "stopping")
                   and row["lease_expires_at"] <= now else row["state"]),
            registered_at=row["registered_at"], heartbeat_at=row["heartbeat_at"],
            lease_expires_at=row["lease_expires_at"], active_slots=row["active_slots"],
        ) for row in rows)


__all__ = [
    "ReviewerWorkerRegistry", "ReviewerWorkerRegistryError",
    "ReviewerWorkerRegistryConflict", "ReviewerWorkerRegistration",
    "ReviewerWorkerStatus",
]
