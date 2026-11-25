"""Durable public-key lifecycle registry for workload credential verification.

Private signing material is intentionally out of scope: signing remains behind
the injected WorkloadSigner/KMS boundary. Only Ed25519 public keys are stored.
"""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import time
from typing import Callable

from core.sqlite_file_security import prepare_private_sqlite_path
from core.workload_identity import WorkloadSigningKey

_STATES = frozenset({"staged", "active", "retired", "revoked"})


class WorkloadKeyRegistryError(PermissionError):
    """Stable key lifecycle rejection."""


class SQLiteWorkloadKeyRegistry:
    """Single-host durable registry implementing WorkloadPublicKeyResolver.

    Multiple keys may be active for one issuer during a rotation overlap.
    Retirement is terminal and requires another active key; revocation is
    immediate and terminal, including when it removes the final active key.
    """

    def __init__(self, database_path: str, *, authorize_admin: Callable[[str], bool],
                 clock: Callable[[], float] = time.time, timeout_seconds: float = 5.0):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("key_registry_path_required")
        if not callable(authorize_admin) or not callable(clock):
            raise TypeError("key_registry_dependencies_invalid")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 30:
            raise ValueError("key_registry_timeout_invalid")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.authorize_admin = authorize_admin
        self.clock = clock
        self.timeout_seconds = float(timeout_seconds)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workload_keys (
                    issuer TEXT NOT NULL, key_id TEXT NOT NULL,
                    algorithm TEXT NOT NULL CHECK(algorithm = 'EdDSA'),
                    public_key BLOB NOT NULL CHECK(length(public_key) = 32),
                    state TEXT NOT NULL CHECK(state IN ('staged','active','retired','revoked')),
                    registered_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    PRIMARY KEY(issuer,key_id)
                );
                CREATE INDEX IF NOT EXISTS workload_keys_lookup
                    ON workload_keys(issuer,key_id,state);
                CREATE TABLE IF NOT EXISTS workload_key_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    administrator TEXT NOT NULL, issuer TEXT NOT NULL,
                    key_id TEXT NOT NULL, action TEXT NOT NULL,
                    previous_state TEXT, new_state TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS workload_key_audit_no_update
                BEFORE UPDATE ON workload_key_audit BEGIN
                    SELECT RAISE(ABORT, 'key_audit_immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS workload_key_audit_no_delete
                BEFORE DELETE ON workload_key_audit BEGIN
                    SELECT RAISE(ABORT, 'key_audit_immutable');
                END;
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=self.timeout_seconds, isolation_level=None)
        try:
            db.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
            yield db
        finally:
            db.close()

    @staticmethod
    def _identity(value, label, maximum=256):
        if (not isinstance(value, str) or not value or len(value) > maximum
            or value != value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
            raise ValueError("key_" + label + "_invalid")

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or value < 0:
            raise ValueError("key_registry_clock_invalid")
        import math
        if not math.isfinite(value):
            raise ValueError("key_registry_clock_invalid")
        return int(value)

    def _admin(self, administrator):
        self._identity(administrator, "administrator")
        try:
            allowed = self.authorize_admin(administrator)
        except Exception as exc:
            raise WorkloadKeyRegistryError("key_admin_authorization_unavailable") from exc
        if allowed is not True:
            raise WorkloadKeyRegistryError("key_admin_forbidden")

    def _record(self, db, administrator, issuer, key_id, action, previous, new, now):
        db.execute("""INSERT INTO workload_key_audit
            (administrator,issuer,key_id,action,previous_state,new_state,occurred_at)
            VALUES(?,?,?,?,?,?,?)""",
            (administrator,issuer,key_id,action,previous,new,now))

    def register(self, administrator: str, key: WorkloadSigningKey) -> None:
        self._admin(administrator)
        if (not isinstance(key, WorkloadSigningKey) or key.algorithm != "EdDSA"
            or len(key.public_key) != 32):
            raise ValueError("key_registration_requires_ed25519_public_key")
        self._identity(key.issuer, "issuer", 512)
        self._identity(key.key_id, "id")
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""INSERT INTO workload_keys
                    (issuer,key_id,algorithm,public_key,state,registered_at,updated_at)
                    VALUES(?,?,?,?,'staged',?,?)""",
                    (key.issuer,key.key_id,key.algorithm,key.public_key,now,now))
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise WorkloadKeyRegistryError("key_id_already_registered") from exc
            self._record(db,administrator,key.issuer,key.key_id,"register",None,"staged",now)
            db.commit()

    def _transition(self, administrator, issuer, key_id, target):
        self._admin(administrator)
        self._identity(issuer,"issuer",512)
        self._identity(key_id,"id")
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM workload_keys WHERE issuer=? AND key_id=?",
                             (issuer,key_id)).fetchone()
            if row is None:
                db.rollback()
                raise WorkloadKeyRegistryError("key_not_found")
            previous = row[0]
            if previous in {"retired","revoked"}:
                db.rollback()
                raise WorkloadKeyRegistryError("key_lifecycle_terminal")
            if target == "active" and previous != "staged":
                db.rollback()
                raise WorkloadKeyRegistryError("key_activation_requires_staged")
            if target == "retired":
                other = db.execute("""SELECT 1 FROM workload_keys
                    WHERE issuer=? AND state='active' AND key_id<>? LIMIT 1""",
                    (issuer,key_id)).fetchone()
                if previous == "active" and other is None:
                    db.rollback()
                    raise WorkloadKeyRegistryError("cannot_retire_last_active_key")
                if previous != "active":
                    db.rollback()
                    raise WorkloadKeyRegistryError("key_retirement_requires_active")
            db.execute("UPDATE workload_keys SET state=?,updated_at=? WHERE issuer=? AND key_id=?",
                       (target,now,issuer,key_id))
            self._record(db,administrator,issuer,key_id,target,previous,target,now)
            db.commit()

    def activate(self, administrator, issuer, key_id):
        self._transition(administrator,issuer,key_id,"active")

    def retire(self, administrator, issuer, key_id):
        self._transition(administrator,issuer,key_id,"retired")

    def revoke(self, administrator, issuer, key_id):
        self._transition(administrator,issuer,key_id,"revoked")

    def resolve(self, issuer: str, key_id: str, algorithm: str) -> WorkloadSigningKey | None:
        self._identity(issuer,"issuer",512)
        self._identity(key_id,"id")
        if algorithm != "EdDSA":
            return None
        with self._connect() as db:
            row = db.execute("""SELECT algorithm,public_key FROM workload_keys
                WHERE issuer=? AND key_id=? AND state='active'""",(issuer,key_id)).fetchone()
        if row is None:
            return None
        return WorkloadSigningKey(issuer,key_id,row[0],bytes(row[1]))

    def audit_events(self, administrator: str, *, after_event_id: int = 0, limit: int = 500):
        self._admin(administrator)
        if type(after_event_id) is not int or after_event_id < 0 or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("workload_key_audit_page_invalid")
        with self._connect() as db:
            rows = db.execute("""SELECT event_id,administrator,issuer,key_id,action,
                previous_state,new_state,occurred_at FROM workload_key_audit
                WHERE event_id>? ORDER BY event_id LIMIT ?""",(after_event_id,limit)).fetchall()
        return tuple(dict(zip(("event_id","administrator","issuer","key_id","action",
            "previous_state","new_state","occurred_at"),row)) for row in rows)


    def close(self) -> None:
        """Compatibility lifecycle hook; SQLite connections are operation-scoped."""
        return None
