"""PostgreSQL-backed shared workload public-key lifecycle registry.

Only Ed25519 public keys are persisted. Private signing material remains behind
the injected signer/KMS boundary. Lifecycle changes and their audit rows commit
atomically; issuer-scoped advisory locks prevent concurrent retirement of the
last active key by independent application replicas.
"""
from __future__ import annotations

import math
from typing import Callable

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from core.workload_identity import WorkloadSigningKey


class WorkloadKeyRegistryError(PermissionError):
    """Stable key lifecycle rejection."""


class PostgresWorkloadKeyRegistry:
    """Shared PostgreSQL implementation of WorkloadPublicKeyResolver."""

    def __init__(
        self,
        database_url: str,
        *,
        authorize_admin: Callable[[str], bool],
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        pool_timeout: float = 10.0,
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("key_registry_database_url_invalid")
        if not callable(authorize_admin):
            raise TypeError("key_registry_authorizer_required")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("key_registry_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not math.isfinite(pool_timeout) or not 1 <= pool_timeout <= 120:
            raise ValueError("key_registry_pool_timeout_invalid")
        self.authorize_admin = authorize_admin
        self.pool = ConnectionPool(
            conninfo=database_url, min_size=min_pool_size, max_size=max_pool_size,
            timeout=float(pool_timeout), kwargs={"row_factory": dict_row}, open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
        except Exception:
            self.pool.close()
            raise

    @staticmethod
    def _identity(value, label, maximum=256):
        if (not isinstance(value, str) or not value or len(value) > maximum
                or value != value.strip()
                or any(ord(char) < 33 or ord(char) > 126 for char in value)):
            raise ValueError("key_" + label + "_invalid")

    def _admin(self, administrator):
        self._identity(administrator, "administrator")
        try:
            allowed = self.authorize_admin(administrator)
        except Exception as exc:
            raise WorkloadKeyRegistryError("key_admin_authorization_unavailable") from exc
        if allowed is not True:
            raise WorkloadKeyRegistryError("key_admin_forbidden")

    @staticmethod
    def _validate_key(key):
        if not isinstance(key, WorkloadSigningKey) or key.algorithm != "EdDSA" or len(key.public_key) != 32:
            raise ValueError("key_registration_requires_ed25519_public_key")
        PostgresWorkloadKeyRegistry._identity(key.issuer, "issuer", 512)
        PostgresWorkloadKeyRegistry._identity(key.key_id, "id")

    def register(self, administrator: str, key: WorkloadSigningKey) -> None:
        self._admin(administrator)
        self._validate_key(key)
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    # Keep the same lock order as lifecycle transitions to avoid
                    # deadlocks when registration races a transition on this issuer.
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key.issuer,))
                    # Serialize audit sequence allocation through transaction commit.
                    # A plain identity sequence can commit out of order across replicas,
                    # causing a monotonic ingestion cursor to skip a late commit.
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (0x59554B49,))
                    conn.execute(
                        """INSERT INTO workload_keys
                           (issuer,key_id,algorithm,public_key,state)
                           VALUES (%s,%s,%s,%s,'staged')""",
                        (key.issuer, key.key_id, key.algorithm, key.public_key),
                    )
                    conn.execute(
                        """INSERT INTO workload_key_audit
                           (administrator,issuer,key_id,action,previous_state,new_state)
                           VALUES (%s,%s,%s,'register',NULL,'staged')""",
                        (administrator, key.issuer, key.key_id),
                    )
        except Exception as exc:
            # Keep duplicate-key failures stable without hiding database outages.
            from psycopg.errors import UniqueViolation
            if isinstance(exc, UniqueViolation):
                raise WorkloadKeyRegistryError("key_id_already_registered") from exc
            raise

    def _transition(self, administrator, issuer, key_id, target):
        self._admin(administrator)
        self._identity(issuer, "issuer", 512)
        self._identity(key_id, "id")
        with self.pool.connection() as conn:
            with conn.transaction():
                # Serialize lifecycle transitions per issuer. This prevents two
                # simultaneous retirements from each observing the other's key.
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (issuer,))
                row = conn.execute(
                    """SELECT state FROM workload_keys
                       WHERE issuer=%s AND key_id=%s FOR UPDATE""",
                    (issuer, key_id),
                ).fetchone()
                if row is None:
                    raise WorkloadKeyRegistryError("key_not_found")
                previous = row["state"]
                if previous in {"retired", "revoked"}:
                    raise WorkloadKeyRegistryError("key_lifecycle_terminal")
                if target == "active" and previous != "staged":
                    raise WorkloadKeyRegistryError("key_activation_requires_staged")
                if target == "retired":
                    other = conn.execute(
                        """SELECT 1 FROM workload_keys
                           WHERE issuer=%s AND state='active' AND key_id<>%s LIMIT 1""",
                        (issuer, key_id),
                    ).fetchone()
                    if previous != "active":
                        raise WorkloadKeyRegistryError("key_retirement_requires_active")
                    if other is None:
                        raise WorkloadKeyRegistryError("cannot_retire_last_active_key")
                # Serialize audit sequence allocation through transaction commit.
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (0x59554B49,))
                conn.execute(
                    """UPDATE workload_keys SET state=%s, updated_at=clock_timestamp()
                       WHERE issuer=%s AND key_id=%s""",
                    (target, issuer, key_id),
                )
                conn.execute(
                    """INSERT INTO workload_key_audit
                       (administrator,issuer,key_id,action,previous_state,new_state)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (administrator, issuer, key_id, target, previous, target),
                )

    def activate(self, administrator, issuer, key_id):
        self._transition(administrator, issuer, key_id, "active")

    def retire(self, administrator, issuer, key_id):
        self._transition(administrator, issuer, key_id, "retired")

    def revoke(self, administrator, issuer, key_id):
        self._transition(administrator, issuer, key_id, "revoked")

    def resolve(self, issuer: str, key_id: str, algorithm: str) -> WorkloadSigningKey | None:
        self._identity(issuer, "issuer", 512)
        self._identity(key_id, "id")
        if algorithm != "EdDSA":
            return None
        with self.pool.connection() as conn:
            row = conn.execute(
                """SELECT algorithm,public_key FROM workload_keys
                   WHERE issuer=%s AND key_id=%s AND state='active'""",
                (issuer, key_id),
            ).fetchone()
        if row is None:
            return None
        return WorkloadSigningKey(issuer, key_id, row["algorithm"], bytes(row["public_key"]))

    def audit_events(
        self, administrator: str, *, after_event_id: int = 0,
        limit: int = 500, issuer: str | None = None,
    ):
        self._admin(administrator)
        if (type(after_event_id) is not int or after_event_id < 0
                or type(limit) is not int or not 1 <= limit <= 5000):
            raise ValueError("workload_key_audit_page_invalid")
        if issuer is not None:
            self._identity(issuer, "issuer", 512)
        with self.pool.connection() as conn:
            if issuer is None:
                rows = conn.execute(
                    """SELECT event_id,administrator,issuer,key_id,action,
                              previous_state,new_state,
                              extract(epoch FROM occurred_at)::double precision AS occurred_at
                       FROM workload_key_audit
                       WHERE event_id>%s ORDER BY event_id LIMIT %s""",
                    (after_event_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT event_id,administrator,issuer,key_id,action,
                              previous_state,new_state,
                              extract(epoch FROM occurred_at)::double precision AS occurred_at
                       FROM workload_key_audit
                       WHERE issuer=%s AND event_id>%s
                       ORDER BY event_id LIMIT %s""",
                    (issuer, after_event_id, limit),
                ).fetchall()
        return tuple(dict(row) for row in rows)

    def check_ready(self):
        """Fail startup unless both lifecycle and audit migrations are installed."""
        with self.pool.connection() as conn:
            conn.execute(
                """SELECT issuer,key_id,algorithm,public_key,state
                   FROM workload_keys LIMIT 0"""
            )
            conn.execute(
                """SELECT event_id,administrator,issuer,key_id,action,
                          previous_state,new_state,occurred_at
                   FROM workload_key_audit LIMIT 0"""
            )
        return True

    def close(self):
        self.pool.close()
