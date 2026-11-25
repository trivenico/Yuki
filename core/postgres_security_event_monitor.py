"""PostgreSQL-backed shared security signals and incident monitor."""
from __future__ import annotations

import hashlib
import math
import re
import time
from datetime import datetime

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from core.security_event_monitor import SecurityEventMonitor, SecuritySignal

_REASON = re.compile(r"^[a-zA-Z0-9_.:-]{1,96}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class PostgresSecurityEventMonitor:
    """Shared monitor implementing the SecurityEventMonitor operational contract."""

    def __init__(
        self,
        database_url: str,
        *,
        authorize_operator,
        failure_threshold: int = 5,
        window_seconds: int = 300,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        pool_timeout: float = 10.0,
        clock=time.time,
    ):
        if not callable(authorize_operator) or not callable(clock):
            raise TypeError("security_monitor_dependencies_required")
        if type(failure_threshold) is not int or not 2 <= failure_threshold <= 1000:
            raise ValueError("security_failure_threshold_invalid")
        if type(window_seconds) is not int or not 1 <= window_seconds <= 86400:
            raise ValueError("security_window_invalid")
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("security_monitor_database_url_invalid")
        if (type(min_pool_size) is not int or type(max_pool_size) is not int
                or not 1 <= min_pool_size <= max_pool_size <= 50):
            raise ValueError("security_monitor_pool_size_invalid")
        if (isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float))
                or not math.isfinite(pool_timeout) or not 1 <= pool_timeout <= 120):
            raise ValueError("security_monitor_pool_timeout_invalid")
        self.authorize_operator = authorize_operator
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.clock = clock
        self.pool = ConnectionPool(
            conninfo=database_url, min_size=min_pool_size, max_size=max_pool_size,
            timeout=float(pool_timeout), kwargs={"row_factory": dict_row}, open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
        except Exception:
            self.pool.close()
            raise

    def _now(self):
        value = self.clock()
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise ValueError("security_monitor_clock_invalid")
        return float(value)

    def check_ready(self):
        with self.pool.connection() as conn:
            conn.execute("SELECT signal_id FROM security_monitor_signals LIMIT 0")
            conn.execute("SELECT incident_id FROM security_monitor_incidents LIMIT 0")
            conn.execute("SELECT history_id FROM security_monitor_incident_history LIMIT 0")
        return True

    def record(self, signal: SecuritySignal):
        SecurityEventMonitor._validate(signal)
        self._now()
        lock_fingerprint = None
        if signal.category in {
            "key_revoked", "audit_integrity_failure", "authorization_denied",
            "admin_auth_denied", "credential_verification_failed",
        } and (signal.subject_hash is not None or signal.category in {"audit_integrity_failure", "key_revoked"}):
            lock_fingerprint = signal.category + ":" + (signal.subject_hash or "global")
        with self.pool.connection() as conn:
            with conn.transaction():
                if lock_fingerprint is not None:
                    # Serialize aggregation for one incident fingerprint across replicas.
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (lock_fingerprint,),
                    )
                inserted = conn.execute(
                    """INSERT INTO security_monitor_signals
                       (signal_id,category,occurred_at,reason_code,subject_hash,source)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (signal_id) DO NOTHING
                       RETURNING event_id""",
                    (signal.signal_id, signal.category, float(signal.occurred_at),
                     signal.reason_code, signal.subject_hash, signal.source),
                ).fetchone()
                if inserted is None:
                    return None

                severity = None
                count = 1
                fingerprint = None
                if signal.category in {"key_revoked", "audit_integrity_failure"}:
                    severity = "critical" if signal.category == "audit_integrity_failure" else "high"
                    fingerprint = signal.category + ":" + (signal.subject_hash or "global")
                elif signal.category in {
                    "authorization_denied", "admin_auth_denied",
                    "credential_verification_failed",
                } and signal.subject_hash:
                    count = conn.execute(
                        """SELECT COUNT(*) AS n FROM security_monitor_signals
                           WHERE category=%s AND subject_hash=%s
                             AND occurred_at >= %s AND occurred_at <= %s""",
                        (signal.category, signal.subject_hash,
                         float(signal.occurred_at) - self.window_seconds,
                         float(signal.occurred_at)),
                    ).fetchone()["n"]
                    if count >= self.failure_threshold:
                        severity = "high"
                        fingerprint = signal.category + ":" + signal.subject_hash

                if fingerprint is None:
                    return None
                incident_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
                row = conn.execute(
                    """INSERT INTO security_monitor_incidents
                       (incident_id,fingerprint,category,severity,status,first_seen,
                        last_seen,event_count,last_reason_code)
                       VALUES (%s,%s,%s,%s,'open',%s,%s,%s,%s)
                       ON CONFLICT (incident_id) DO UPDATE SET
                         first_seen=LEAST(security_monitor_incidents.first_seen, EXCLUDED.first_seen),
                         last_seen=GREATEST(security_monitor_incidents.last_seen, EXCLUDED.last_seen),
                         event_count=GREATEST(security_monitor_incidents.event_count,
                                              EXCLUDED.event_count),
                         last_reason_code=CASE
                           WHEN EXCLUDED.last_seen >= security_monitor_incidents.last_seen
                           THEN EXCLUDED.last_reason_code
                           ELSE security_monitor_incidents.last_reason_code END,
                         status=CASE WHEN security_monitor_incidents.status='resolved'
                                     THEN 'open' ELSE security_monitor_incidents.status END
                       RETURNING *""",
                    (incident_id, fingerprint, signal.category, severity,
                     float(signal.occurred_at), float(signal.occurred_at),
                     int(count), signal.reason_code),
                ).fetchone()
                return dict(row)

    def ingest_authorization_audit(self, audit_log, *, after_event_id=0, limit=500):
        """Import denied decisions from any authoritative audit repository page."""
        if (not callable(getattr(audit_log, "event_page", None))
                or type(after_event_id) is not int or after_event_id < 0
                or type(limit) is not int or not 1 <= limit <= 5000):
            raise ValueError("authorization_audit_source_invalid")
        rows = audit_log.event_page(after_event_id=after_event_id, limit=limit)
        highest = after_event_id
        for row in rows:
            event_id = row.get("event_id")
            if type(event_id) is not int or event_id <= after_event_id:
                continue
            highest = max(highest, event_id)
            if row.get("allowed") != 0:
                continue
            stamp = datetime.fromisoformat(row["occurred_at"])
            if stamp.tzinfo is None:
                raise ValueError("authorization_audit_timestamp_invalid")
            subject_hash = row.get("principal_hash")
            if not isinstance(subject_hash, str) or not _HASH.fullmatch(subject_hash):
                raise ValueError("authorization_audit_subject_hash_invalid")
            reason_code = row.get("reason_code")
            reason = reason_code if isinstance(reason_code, str) and _REASON.fullmatch(reason_code) else "authorization_denied"
            self.record(SecuritySignal(
                "authorization-audit:" + str(event_id), "authorization_denied",
                stamp.timestamp(), reason, subject_hash, "authorization_audit",
            ))
        return highest

    def ingest_key_lifecycle(self, registry, administrator=None, *, after_event_id=0, limit=500):
        if (type(after_event_id) is not int or after_event_id < 0
                or type(limit) is not int or not 1 <= limit <= 5000):
            raise ValueError("key_audit_cursor_invalid")
        # Legacy lifecycle registries authorize an administrator for audit reads.
        # The dedicated PostgreSQL audit projection has no administrator surface:
        # its separate SELECT-only database identity is the access boundary.
        if administrator is None:
            rows = registry.audit_events(after_event_id=after_event_id, limit=limit)
        else:
            rows = registry.audit_events(administrator, after_event_id=after_event_id, limit=limit)
        highest = after_event_id
        for row in rows:
            event_id = row.get("event_id")
            if type(event_id) is not int or event_id <= after_event_id:
                continue
            highest = max(highest, event_id)
            if row.get("action") not in {"revoked", "revoke"}:
                continue
            subject = hashlib.sha256((row["issuer"] + ":" + row["key_id"]).encode()).hexdigest()
            self.record(SecuritySignal(
                "workload-key-audit:" + str(event_id), "key_revoked",
                float(row["occurred_at"]), "key_revoked", subject, "workload_key_registry",
            ))
        return highest

    def _authorize(self, operator):
        if not isinstance(operator, str) or not operator or len(operator) > 256 or operator != operator.strip():
            raise PermissionError("security_operator_invalid")
        try:
            allowed = self.authorize_operator(operator)
        except Exception as exc:
            raise PermissionError("security_operator_authorization_unavailable") from exc
        if allowed is not True:
            raise PermissionError("security_operator_forbidden")
        return hashlib.sha256(operator.encode()).hexdigest()

    def incidents(self, operator, *, status=None, limit=100):
        self._authorize(operator)
        if status is not None and status not in {"open", "acknowledged", "resolved"}:
            raise ValueError("security_incident_status_invalid")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("security_incident_limit_invalid")
        sql = "SELECT * FROM security_monitor_incidents"
        params = ()
        if status is not None:
            sql += " WHERE status=%s"
            params = (status,)
        sql += " ORDER BY last_seen DESC LIMIT %s"
        params += (limit,)
        with self.pool.connection() as conn:
            return tuple(dict(row) for row in conn.execute(sql, params).fetchall())

    def transition(self, operator, incident_id, action):
        operator_hash = self._authorize(operator)
        if not isinstance(incident_id, str) or not re.fullmatch(r"[0-9a-f]{32}", incident_id):
            raise ValueError("security_incident_id_invalid")
        if action not in {"acknowledge", "resolve"}:
            raise ValueError("security_incident_action_invalid")
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT status FROM security_monitor_incidents WHERE incident_id=%s FOR UPDATE",
                    (incident_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("security_incident_not_found")
                if row["status"] == "resolved":
                    raise ValueError("security_incident_already_resolved")
                status = "acknowledged" if action == "acknowledge" else "resolved"
                conn.execute(
                    "UPDATE security_monitor_incidents SET status=%s,updated_by=%s WHERE incident_id=%s",
                    (status, operator_hash, incident_id),
                )
                conn.execute(
                    """INSERT INTO security_monitor_incident_history
                       (incident_id,action,operator_hash,occurred_at) VALUES (%s,%s,%s,%s)""",
                    (incident_id, action, operator_hash, self._now()),
                )

    def close(self):
        self.pool.close()
