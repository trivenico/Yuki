"""Real PostgreSQL integration tests for shared security monitor state."""
import os
from pathlib import Path
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresSecurityEventMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.postgres_security_event_monitor import PostgresSecurityEventMonitor
        cls.monitor_type = PostgresSecurityEventMonitor
        root = Path(__file__).resolve().parents[1]
        with psycopg.connect(POSTGRES_URL) as conn:
            for migration in (
                root / "migrations/security/0004_workload_key_registry.sql",
                root / "migrations/security/0005_security_monitor.sql",
            ):
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        self.operator = "security-operator"
        self.monitor_a = self.monitor_type(
            POSTGRES_URL, authorize_operator=lambda value: value == self.operator,
            failure_threshold=3,
        )
        self.monitor_b = self.monitor_type(
            POSTGRES_URL, authorize_operator=lambda value: value == self.operator,
            failure_threshold=3,
        )
        self.subject = uuid.uuid4().hex * 2

    def tearDown(self):
        self.monitor_a.close()
        self.monitor_b.close()

    def test_signals_and_incidents_are_shared_between_independent_pools(self):
        from core.security_event_monitor import SecuritySignal
        incident = None
        for index in range(3):
            incident = self.monitor_a.record(SecuritySignal(
                "pg-monitor-" + uuid.uuid4().hex,
                "authorization_denied", 1000.0 + index, "policy_denied",
                self.subject, "authorization_audit",
            ))
        self.assertIsNotNone(incident)
        self.assertEqual(incident["event_count"], 3)
        shared = self.monitor_b.incidents(self.operator, status="open")
        match = [row for row in shared if row["incident_id"] == incident["incident_id"]]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["event_count"], 3)

    def test_duplicate_signal_is_idempotent_across_replicas(self):
        from core.security_event_monitor import SecuritySignal
        signal = SecuritySignal(
            "pg-monitor-dedupe-" + uuid.uuid4().hex,
            "key_revoked", 1200.0, "key_revoked", self.subject,
            "workload_key_registry",
        )
        self.assertIsNotNone(self.monitor_a.record(signal))
        self.assertIsNone(self.monitor_b.record(signal))
        rows = self.monitor_b.incidents(self.operator, status="open")
        matches = [row for row in rows if row["fingerprint"] == "key_revoked:" + self.subject]
        self.assertEqual(len(matches), 1)

    def test_incident_transition_and_history_are_shared_and_immutable(self):
        from core.security_event_monitor import SecuritySignal
        incident = self.monitor_a.record(SecuritySignal(
            "pg-monitor-transition-" + uuid.uuid4().hex,
            "audit_integrity_failure", 1300.0, "checkpoint_mismatch",
            None, "audit_anchor",
        ))
        self.monitor_a.transition(self.operator, incident["incident_id"], "acknowledge")
        shared = self.monitor_b.incidents(self.operator, status="acknowledged")
        self.assertIn(incident["incident_id"], {row["incident_id"] for row in shared})
        with psycopg.connect(POSTGRES_URL) as conn:
            with self.assertRaises(psycopg.Error):
                conn.execute(
                    "UPDATE security_monitor_incident_history SET action='resolve' WHERE incident_id=%s",
                    (incident["incident_id"],),
                )

    def test_operator_authorization_fails_closed(self):
        from core.security_event_monitor import SecuritySignal
        with self.assertRaisesRegex(PermissionError, "security_operator_forbidden"):
            self.monitor_a.incidents("untrusted", status="open")
        with self.assertRaisesRegex(PermissionError, "security_operator_forbidden"):
            self.monitor_a.transition("untrusted", "0" * 32, "resolve")

    def test_key_revocation_ingestion_creates_shared_incident(self):
        from core.postgres_workload_key_registry import PostgresWorkloadKeyRegistry
        from core.security_event_monitor import SecuritySignal
        from core.workload_identity import WorkloadSigningKey

        issuer = "pg-monitor-" + uuid.uuid4().hex
        registry = PostgresWorkloadKeyRegistry(
            POSTGRES_URL, authorize_admin=lambda value: value == "security-admin",
        )
        try:
            key = WorkloadSigningKey(issuer, "revoked-key", "EdDSA", b"z" * 32)
            registry.register("security-admin", key)
            registry.activate("security-admin", issuer, key.key_id)
            registry.revoke("security-admin", issuer, key.key_id)
            cursor = self.monitor_a.ingest_key_lifecycle(
                registry, "security-admin", after_event_id=0, limit=20,
            )
            self.assertGreaterEqual(cursor, 3)
            incidents = self.monitor_b.incidents("security-operator", status="open")
            subject = __import__("hashlib").sha256(
                (issuer + ":" + key.key_id).encode()
            ).hexdigest()
            self.assertIn(
                "key_revoked:" + subject,
                {row["fingerprint"] for row in incidents},
            )
        finally:
            registry.close()


if __name__ == "__main__":
    unittest.main()
