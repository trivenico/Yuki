"""PostgreSQL-backed at-least-once security alert delivery outbox."""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from core.security_alert_outbox import SecurityAlertOutbox


_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE = re.compile(r"^[a-zA-Z0-9_.:-]{1,96}$")
_STATES = frozenset({"pending", "inflight", "delivered", "dead"})


class PostgresSecurityAlertOutbox:
    """Shared outbox with atomic SKIP LOCKED claims and lease-fenced acknowledgements."""

    def __init__(
        self, database_url, *, authorize_operator,
        base_retry_seconds=5, max_retry_seconds=3600,
        max_attempts=12, lease_seconds=60,
        min_pool_size=1, max_pool_size=10, pool_timeout=10.0,
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("alert_outbox_database_url_invalid")
        if not callable(authorize_operator):
            raise TypeError("alert_outbox_authorizer_required")
        for name, value, low, high in (
            ("base_retry_seconds", base_retry_seconds, 1, 3600),
            ("max_retry_seconds", max_retry_seconds, 1, 86400),
            ("max_attempts", max_attempts, 1, 100),
            ("lease_seconds", lease_seconds, 5, 3600),
            ("min_pool_size", min_pool_size, 1, 50),
            ("max_pool_size", max_pool_size, 1, 50),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("alert_outbox_" + name + "_invalid")
        if max_retry_seconds < base_retry_seconds or min_pool_size > max_pool_size:
            raise ValueError("alert_outbox_configuration_invalid")
        if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
            raise ValueError("alert_outbox_pool_timeout_invalid")
        self.authorize_operator = authorize_operator
        self.base_retry_seconds = base_retry_seconds
        self.max_retry_seconds = max_retry_seconds
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
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
    def _payload(incident):
        return SecurityAlertOutbox._incident_payload(incident)

    def enqueue_incident(self, incident):
        payload = self._payload(incident)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        alert_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
        sql = """
            INSERT INTO security_alert_outbox
                (alert_id, incident_id, payload, state, created_at, available_at)
            VALUES (%s, %s, %s, 'pending', clock_timestamp(), clock_timestamp())
            ON CONFLICT (alert_id) DO NOTHING
            RETURNING alert_id
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(sql, (alert_id, payload["incident_id"], Jsonb(payload)))
        return alert_id

    def sync_incidents(self, monitor, operator, *, limit=100):
        if not callable(getattr(monitor, "incidents", None)):
            raise TypeError("security_event_monitor_required")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("alert_sync_limit_invalid")
        alert_ids = []
        for status in ("open", "acknowledged"):
            for incident in monitor.incidents(operator, status=status, limit=limit):
                alert_ids.append(self.enqueue_incident(incident))
        return tuple(alert_ids)

    def claim(self, worker_id):
        if not isinstance(worker_id, str) or not _SAFE.fullmatch(worker_id):
            raise ValueError("alert_worker_id_invalid")
        sql = """
            WITH candidate AS (
                SELECT alert_id
                FROM security_alert_outbox
                WHERE (
                    (state = 'pending' AND available_at <= clock_timestamp())
                    OR (state = 'inflight' AND lease_until <= clock_timestamp() AND attempts < %s)
                )
                ORDER BY available_at, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE security_alert_outbox AS outbox
            SET state = 'inflight',
                attempts = outbox.attempts + 1,
                lease_epoch = outbox.lease_epoch + 1,
                lease_owner = %s,
                lease_token = %s,
                lease_until = clock_timestamp() + (%s * interval '1 second')
            FROM candidate
            WHERE outbox.alert_id = candidate.alert_id
            RETURNING outbox.alert_id, outbox.payload, outbox.attempts, outbox.lease_token, outbox.lease_epoch
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    """UPDATE security_alert_outbox
                       SET state='dead', lease_owner=NULL, lease_token=NULL, lease_until=NULL,
                           last_error_code='lease_expired'
                       WHERE state='inflight' AND lease_until <= clock_timestamp() AND attempts >= %s""",
                    (self.max_attempts,),
                )
                row = conn.execute(sql, (self.max_attempts, worker_id, uuid.uuid4().hex,
                                         self.lease_seconds)).fetchone()
        if row is None:
            return None
        payload = row["payload"]
        if not isinstance(payload, dict):
            payload = json.loads(payload)
        return {
            "alert_id": row["alert_id"], "payload": payload,
            "attempt": int(row["attempts"]), "lease_token": row["lease_token"],
            "lease_epoch": int(row["lease_epoch"]),
        }

    def complete(self, worker_id, alert_id, *, lease_token, lease_epoch, success, error_code="delivery_failed"):
        if not isinstance(worker_id, str) or not _SAFE.fullmatch(worker_id):
            raise ValueError("alert_worker_id_invalid")
        if not isinstance(alert_id, str) or not _ID.fullmatch(alert_id):
            raise ValueError("alert_id_invalid")
        if not isinstance(lease_token, str) or not _ID.fullmatch(lease_token):
            raise ValueError("alert_lease_token_invalid")
        if type(lease_epoch) is not int or lease_epoch < 1:
            raise ValueError("alert_lease_epoch_invalid")
        if type(success) is not bool:
            raise TypeError("alert_delivery_result_must_be_boolean")
        if not isinstance(error_code, str) or not _SAFE.fullmatch(error_code):
            raise ValueError("alert_error_code_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """SELECT attempts FROM security_alert_outbox
                       WHERE alert_id=%s AND state='inflight' AND lease_owner=%s
                         AND lease_token=%s AND lease_epoch=%s AND lease_until > clock_timestamp()
                       FOR UPDATE""",
                    (alert_id, worker_id, lease_token, lease_epoch),
                ).fetchone()
                if row is None:
                    exists = conn.execute(
                        "SELECT 1 FROM security_alert_outbox WHERE alert_id=%s", (alert_id,)
                    ).fetchone()
                    if exists is None:
                        raise KeyError("alert_not_found")
                    raise PermissionError("alert_lease_not_owned")
                attempts = int(row["attempts"])
                if success:
                    conn.execute(
                        """UPDATE security_alert_outbox SET state='delivered',
                           delivered_at=clock_timestamp(), lease_owner=NULL, lease_token=NULL,
                           lease_until=NULL, last_error_code=NULL WHERE alert_id=%s""",
                        (alert_id,),
                    )
                    return "delivered"
                if attempts >= self.max_attempts:
                    conn.execute(
                        """UPDATE security_alert_outbox SET state='dead', lease_owner=NULL,
                           lease_token=NULL, lease_until=NULL, last_error_code=%s WHERE alert_id=%s""",
                        (error_code, alert_id),
                    )
                    return "dead"
                delay = min(self.max_retry_seconds, self.base_retry_seconds * (2 ** min(attempts - 1, 20)))
                conn.execute(
                    """UPDATE security_alert_outbox SET state='pending',
                       available_at=clock_timestamp() + (%s * interval '1 second'),
                       lease_owner=NULL, lease_token=NULL, lease_until=NULL,
                       last_error_code=%s WHERE alert_id=%s""",
                    (delay, error_code, alert_id),
                )
                return "pending"

    def deliver_one(self, worker_id, sender):
        if not callable(sender):
            raise TypeError("alert_sender_required")
        item = self.claim(worker_id)
        if item is None:
            return "empty"
        try:
            success = sender(item["payload"], item["alert_id"])
            if type(success) is not bool:
                raise TypeError("alert_sender_must_return_boolean")
        except Exception:
            return self.complete(
                worker_id, item["alert_id"], lease_token=item["lease_token"],
                lease_epoch=item["lease_epoch"], success=False, error_code="provider_error",
            )
        return self.complete(
            worker_id, item["alert_id"], lease_token=item["lease_token"],
            lease_epoch=item["lease_epoch"], success=success, error_code="provider_rejected",
        )

    def list_alerts(self, operator, *, state=None, limit=100):
        if not isinstance(operator, str) or not operator or len(operator) > 256 or operator != operator.strip():
            raise PermissionError("alert_operator_invalid")
        try:
            allowed = self.authorize_operator(operator)
        except Exception as exc:
            raise PermissionError("alert_operator_authorization_unavailable") from exc
        if allowed is not True:
            raise PermissionError("alert_operator_forbidden")
        if state is not None and state not in _STATES:
            raise ValueError("alert_state_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("alert_limit_invalid")
        sql = """SELECT alert_id, incident_id, state, attempts, created_at, available_at,
                        last_error_code, delivered_at
                 FROM security_alert_outbox"""
        params = ()
        if state is not None:
            sql += " WHERE state=%s"
            params = (state,)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params += (limit,)
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(dict(row) for row in rows)

    def close(self):
        self.pool.close()
