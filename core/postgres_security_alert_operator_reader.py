"""Read-only PostgreSQL projection for security alert operators.

This repository deliberately has no delivery methods and never selects payload
or lease credentials. Its LOGIN principal should receive SELECT on only the
operator projection columns and aggregate-health columns.
"""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_STATES = frozenset({"pending", "inflight", "delivered", "dead"})


class PostgresSecurityAlertOperatorReader:
    def __init__(self, database_url, *, authorize_operator,
                 min_pool_size=1, max_pool_size=10, pool_timeout=10.0):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("alert_operator_reader_database_url_invalid")
        if not callable(authorize_operator):
            raise TypeError("alert_operator_reader_authorizer_required")
        if (type(min_pool_size) is not int or not 1 <= min_pool_size <= 50
                or type(max_pool_size) is not int or not min_pool_size <= max_pool_size <= 50):
            raise ValueError("alert_operator_reader_pool_size_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("alert_operator_reader_pool_timeout_invalid")
        self.authorize_operator = authorize_operator
        self.pool = ConnectionPool(
            conninfo=database_url, min_size=min_pool_size, max_size=max_pool_size,
            timeout=float(pool_timeout), kwargs={"row_factory": dict_row}, open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
        except Exception:
            self.pool.close()
            raise

    def _authorize(self, operator):
        if not isinstance(operator, str) or not operator or len(operator) > 256 or operator != operator.strip():
            raise PermissionError("alert_operator_invalid")
        try:
            allowed = self.authorize_operator(operator)
        except Exception as exc:
            raise PermissionError("alert_operator_authorization_unavailable") from exc
        if allowed is not True:
            raise PermissionError("alert_operator_forbidden")

    def list_alerts(self, operator, *, state=None, limit=100):
        self._authorize(operator)
        if state is not None and state not in _STATES:
            raise ValueError("alert_state_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("alert_limit_invalid")
        query = """SELECT alert_id, incident_id, state, attempts, created_at,
                          available_at, last_error_code, delivered_at
                   FROM security_alert_outbox"""
        params = ()
        if state is not None:
            query += " WHERE state=%s"
            params = (state,)
        query += " ORDER BY created_at DESC LIMIT %s"
        with self.pool.connection() as conn:
            rows = conn.execute(query, params + (limit,)).fetchall()
        return tuple(dict(row) for row in rows)

    def health_snapshot(self, operator):
        self._authorize(operator)
        query = """
            SELECT
                count(*) FILTER (WHERE state='pending')::bigint AS pending,
                count(*) FILTER (WHERE state='inflight')::bigint AS inflight,
                count(*) FILTER (WHERE state='delivered')::bigint AS delivered,
                count(*) FILTER (WHERE state='dead')::bigint AS dead,
                count(*) FILTER (WHERE state='inflight' AND lease_until <= clock_timestamp())::bigint AS expired_leases,
                CASE WHEN min(created_at) FILTER (WHERE state='pending') IS NULL THEN NULL
                     ELSE greatest(0, extract(epoch FROM (
                         clock_timestamp() - min(created_at) FILTER (WHERE state='pending')
                     ))) END AS oldest_pending_age_seconds
            FROM security_alert_outbox
        """
        with self.pool.connection() as conn:
            row = conn.execute(query).fetchone()
        return {
            "pending": int(row["pending"]), "inflight": int(row["inflight"]),
            "delivered": int(row["delivered"]), "dead": int(row["dead"]),
            "expired_leases": int(row["expired_leases"]),
            "oldest_pending_age_seconds": (
                float(row["oldest_pending_age_seconds"])
                if row["oldest_pending_age_seconds"] is not None else None
            ),
        }

    def check_ready(self):
        with self.pool.connection() as conn:
            conn.execute(
                "SELECT alert_id, incident_id, state, attempts, created_at, available_at, "
                "last_error_code, delivered_at, lease_until FROM security_alert_outbox LIMIT 0"
            )
        return True

    def close(self):
        self.pool.close()
