"""Read-only PostgreSQL projections for security audit ingestion workers."""
from __future__ import annotations

import math

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class _ReadOnlyAuditReader:
    def __init__(self, database_url: str, *, min_pool_size: int = 1,
                 max_pool_size: int = 5, pool_timeout: float = 10.0) -> None:
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("security_audit_reader_database_url_invalid")
        if (type(min_pool_size) is not int or type(max_pool_size) is not int
                or not 1 <= min_pool_size <= max_pool_size <= 20):
            raise ValueError("security_audit_reader_pool_size_invalid")
        if (isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float))
                or not math.isfinite(pool_timeout) or not 1 <= pool_timeout <= 120):
            raise ValueError("security_audit_reader_pool_timeout_invalid")
        self._pool = ConnectionPool(
            conninfo=database_url, min_size=min_pool_size, max_size=max_pool_size,
            timeout=float(pool_timeout), kwargs={"row_factory": dict_row}, open=True,
        )

    def close(self) -> None:
        self._pool.close()

    def _read(self, sql: str, params: tuple = ()):
        # Enforce read-only at the transaction boundary in addition to the
        # deployment-owned PostgreSQL role's SELECT-only grants.
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                return conn.execute(sql, params).fetchall()


class PostgresAuthorizationAuditReader(_ReadOnlyAuditReader):
    """Minimal event projection; intentionally has no audit append API."""

    def check_ready(self) -> bool:
        self._read("SELECT event_id FROM authorization_audit_events LIMIT 0")
        self._read("SELECT singleton FROM authorization_audit_state WHERE singleton=1")
        return True

    def event_page(self, *, after_event_id: int = 0, limit: int = 500) -> tuple[dict, ...]:
        if type(after_event_id) is not int or after_event_id < 0 or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("authorization_audit_page_invalid")
        rows = self._read(
            "SELECT event_id, occurred_at, principal_hash, reason_code, allowed "
            "FROM authorization_audit_events WHERE event_id>%s ORDER BY event_id LIMIT %s",
            (after_event_id, limit),
        )
        return tuple(dict(row) for row in rows)


class PostgresWorkloadKeyAuditReader(_ReadOnlyAuditReader):
    """Read-only lifecycle audit projection; exposes no key resolution or mutation."""

    def check_ready(self) -> bool:
        self._read("SELECT event_id FROM workload_key_audit LIMIT 0")
        return True

    def audit_events(self, worker_id: str, *, after_event_id: int = 0,
                     limit: int = 500) -> tuple[dict, ...]:
        if (not isinstance(worker_id, str) or not worker_id or len(worker_id) > 96
                or worker_id != worker_id.strip()):
            raise ValueError("security_audit_reader_worker_invalid")
        if type(after_event_id) is not int or after_event_id < 0 or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("workload_key_audit_page_invalid")
        rows = self._read(
            """SELECT event_id, administrator, issuer, key_id, action,
                      previous_state, new_state,
                      extract(epoch FROM occurred_at)::double precision AS occurred_at
               FROM workload_key_audit
               WHERE event_id>%s ORDER BY event_id LIMIT %s""",
            (after_event_id, limit),
        )
        return tuple(dict(row) for row in rows)
