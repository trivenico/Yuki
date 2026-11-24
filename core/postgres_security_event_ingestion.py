"""PostgreSQL-backed durable security ingestion cursors.

This repository implements the same small cursor contract as the SQLite
single-host store while using database time and fenced ownership for replicas.
"""
from __future__ import annotations

import re
import uuid

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


_SOURCE = frozenset({"authorization_audit", "workload_key_audit"})
_WORKER = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_ERROR = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class PostgresSecurityEventIngestionCursors:
    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        pool_timeout: float = 10.0,
        lease_seconds: int = 60,
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("security_ingestion_database_url_invalid")
        if type(min_pool_size) is not int or type(max_pool_size) is not int or not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("security_ingestion_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("security_ingestion_pool_timeout_invalid")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("security_ingestion_lease_duration_invalid")
        self.lease_seconds = lease_seconds
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
    def _validate_source(source):
        if not isinstance(source, str) or source not in _SOURCE:
            raise ValueError("security_ingestion_source_invalid")

    @staticmethod
    def _validate_worker(worker_id):
        if not isinstance(worker_id, str) or not _WORKER.fullmatch(worker_id):
            raise ValueError("security_ingestion_worker_invalid")

    @staticmethod
    def _validate_claim(claim):
        if (not isinstance(claim, dict) or claim.get("source") not in _SOURCE
                or not isinstance(claim.get("lease_token"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", claim["lease_token"])
                or type(claim.get("lease_epoch")) is not int
                or claim["lease_epoch"] < 1
                or type(claim.get("event_id")) is not int
                or claim["event_id"] < 0):
            raise ValueError("security_ingestion_claim_invalid")

    def claim(self, source, worker_id):
        self._validate_source(source)
        self._validate_worker(worker_id)
        token = uuid.uuid4().hex
        sql = """
            INSERT INTO security_ingestion_cursors (source)
            VALUES (%s) ON CONFLICT (source) DO NOTHING
        """
        claim_sql = """
            UPDATE security_ingestion_cursors
            SET lease_owner = %s, lease_token = %s,
                lease_epoch = lease_epoch + 1,
                lease_until = clock_timestamp() + (%s * interval '1 second'),
                last_error_code = NULL
            WHERE source = %s
              AND (lease_until IS NULL OR lease_until <= clock_timestamp())
            RETURNING event_id, lease_epoch
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(sql, (source,))
                row = conn.execute(claim_sql, (worker_id, token, self.lease_seconds, source)).fetchone()
        if row is None:
            return None
        return {
            "source": source,
            "event_id": int(row["event_id"]),
            "lease_token": token,
            "lease_epoch": int(row["lease_epoch"]),
        }

    def complete(self, claim, worker_id, event_id):
        self._validate_claim(claim)
        self._validate_worker(worker_id)
        if type(event_id) is not int or event_id < claim["event_id"]:
            raise ValueError("security_ingestion_cursor_advance_invalid")
        sql = """
            UPDATE security_ingestion_cursors
            SET event_id = %s, last_success_at = clock_timestamp(),
                last_error_code = NULL, lease_owner = NULL,
                lease_token = NULL, lease_until = NULL
            WHERE source = %s AND event_id <= %s
              AND lease_owner = %s AND lease_token = %s AND lease_epoch = %s
              AND lease_until > clock_timestamp()
            RETURNING event_id
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(sql, (
                    event_id, claim["source"], event_id, worker_id,
                    claim["lease_token"], claim["lease_epoch"],
                )).fetchone()
        if row is None:
            raise PermissionError("security_ingestion_lease_lost")
        return int(row["event_id"])

    def fail(self, claim, worker_id, error_code="source_ingestion_failed"):
        self._validate_claim(claim)
        self._validate_worker(worker_id)
        if not isinstance(error_code, str) or not _ERROR.fullmatch(error_code):
            raise ValueError("security_ingestion_error_code_invalid")
        sql = """
            UPDATE security_ingestion_cursors
            SET last_error_code = %s, lease_owner = NULL,
                lease_token = NULL, lease_until = NULL
            WHERE source = %s AND lease_owner = %s
              AND lease_token = %s AND lease_epoch = %s
              AND lease_until > clock_timestamp()
            RETURNING source
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(sql, (
                    error_code, claim["source"], worker_id,
                    claim["lease_token"], claim["lease_epoch"],
                )).fetchone()
        if row is None:
            raise PermissionError("security_ingestion_lease_lost")

    def snapshot(self):
        sql = """
            SELECT source, event_id, last_success_at, last_error_code,
                   (lease_until > clock_timestamp()) AS lease_active
            FROM security_ingestion_cursors ORDER BY source
        """
        with self.pool.connection() as conn:
            rows = conn.execute(sql).fetchall()
        return {
            row["source"]: {
                "event_id": int(row["event_id"]),
                "last_success_at": row["last_success_at"].timestamp() if row["last_success_at"] else None,
                "last_error_code": row["last_error_code"],
                "lease_active": bool(row["lease_active"]),
            }
            for row in rows
        }

    def check_ready(self):
        with self.pool.connection() as conn:
            conn.execute("SELECT source, event_id, lease_owner, lease_token, lease_epoch, lease_until FROM security_ingestion_cursors LIMIT 0")
        return True

    def close(self):
        self.pool.close()
