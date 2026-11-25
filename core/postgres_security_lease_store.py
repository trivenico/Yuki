"""PostgreSQL-backed distributed leases for security pipeline work.

Schema is managed by versioned SQL migrations. Claims and ownership changes use
short transactions and PostgreSQL time; provider/network work must happen after
the claim transaction commits.
"""
from __future__ import annotations

from core.postgres_database_identity import postgres_database_identity

from dataclasses import dataclass
import hashlib
import re
import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_RESOURCE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,200}$")


@dataclass(frozen=True)
class SecurityWorkLease:
    queue_name: str
    resource_key: str
    worker_id: str
    lease_token: str
    lease_epoch: int
    lease_expires_at: Any


class PostgresSecurityLeaseStore:
    """Atomic lease claims with owner, random token, and monotonic fencing epoch."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        pool_timeout: float = 10.0,
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("security_lease_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int:
            raise ValueError("security_lease_pool_size_invalid")
        if not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("security_lease_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("security_lease_pool_timeout_invalid")
        self.database_identity = postgres_database_identity(database_url)
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=float(pool_timeout),
            kwargs={"row_factory": dict_row},
            open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
        except Exception:
            self.pool.close()
            raise

    @staticmethod
    def _validate(queue_name: str, resource_key: str, worker_id: str, lease_seconds: int) -> None:
        if not isinstance(queue_name, str) or not _NAME.fullmatch(queue_name):
            raise ValueError("security_lease_queue_invalid")
        if not isinstance(resource_key, str) or not _RESOURCE.fullmatch(resource_key):
            raise ValueError("security_lease_resource_invalid")
        if not isinstance(worker_id, str) or not _NAME.fullmatch(worker_id):
            raise ValueError("security_lease_worker_invalid")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("security_lease_duration_invalid")

    @staticmethod
    def _lease(row: dict | None) -> SecurityWorkLease | None:
        if row is None:
            return None
        return SecurityWorkLease(
            queue_name=row["queue_name"],
            resource_key=row["resource_key"],
            worker_id=row["lease_owner"],
            lease_token=row["lease_token"],
            lease_epoch=int(row["lease_epoch"]),
            lease_expires_at=row["lease_expires_at"],
        )

    def claim(
        self, queue_name: str, resource_key: str, worker_id: str, *, lease_seconds: int = 60
    ) -> SecurityWorkLease | None:
        self._validate(queue_name, resource_key, worker_id, lease_seconds)
        token = uuid.uuid4().hex
        sql = """
            INSERT INTO security_work_leases
                (queue_name, resource_key, lease_owner, lease_token, lease_epoch,
                 lease_expires_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 1,
                    clock_timestamp() + (%s * interval '1 second'),
                    clock_timestamp(), clock_timestamp())
            ON CONFLICT (queue_name, resource_key) DO UPDATE SET
                lease_owner = EXCLUDED.lease_owner,
                lease_token = EXCLUDED.lease_token,
                lease_epoch = security_work_leases.lease_epoch + 1,
                lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                updated_at = clock_timestamp()
            WHERE security_work_leases.lease_expires_at <= clock_timestamp()
            RETURNING queue_name, resource_key, lease_owner, lease_token,
                      lease_epoch, lease_expires_at
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(sql, (queue_name, resource_key, worker_id, token,
                                         lease_seconds, lease_seconds)).fetchone()
        return self._lease(row)

    def renew(
        self, lease: SecurityWorkLease, *, lease_seconds: int = 60
    ) -> SecurityWorkLease | None:
        if not isinstance(lease, SecurityWorkLease):
            raise ValueError("security_lease_claim_invalid")
        self._validate(lease.queue_name, lease.resource_key, lease.worker_id, lease_seconds)
        sql = """
            UPDATE security_work_leases
            SET lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                updated_at = clock_timestamp()
            WHERE queue_name = %s AND resource_key = %s
              AND lease_owner = %s AND lease_token = %s AND lease_epoch = %s
              AND lease_expires_at > clock_timestamp()
            RETURNING queue_name, resource_key, lease_owner, lease_token,
                      lease_epoch, lease_expires_at
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(sql, (lease_seconds, lease.queue_name, lease.resource_key,
                                         lease.worker_id, lease.lease_token,
                                         lease.lease_epoch)).fetchone()
        return self._lease(row)

    def release(self, lease: SecurityWorkLease) -> bool:
        if not isinstance(lease, SecurityWorkLease):
            raise ValueError("security_lease_claim_invalid")
        self._validate(lease.queue_name, lease.resource_key, lease.worker_id, 5)
        sql = """
            UPDATE security_work_leases
            SET lease_expires_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE queue_name = %s AND resource_key = %s
              AND lease_owner = %s AND lease_token = %s AND lease_epoch = %s
              AND lease_expires_at > clock_timestamp()
            RETURNING lease_epoch
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(sql, (lease.queue_name, lease.resource_key,
                                         lease.worker_id, lease.lease_token,
                                         lease.lease_epoch)).fetchone()
        return row is not None

    def check_ready(self):
        with self.pool.connection() as conn:
            conn.execute("SELECT queue_name, resource_key, lease_epoch FROM security_work_leases LIMIT 0")
        return True

    def close(self) -> None:
        """Close the bounded connection pool during host shutdown."""
        self.pool.close()
