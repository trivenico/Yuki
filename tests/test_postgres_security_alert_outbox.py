"""Real PostgreSQL integration tests for shared alert outbox claims and fencing."""
import os
import threading
from pathlib import Path
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresSecurityAlertOutboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.postgres_security_alert_outbox import PostgresSecurityAlertOutbox
        cls.store_type = PostgresSecurityAlertOutbox
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0003_security_alert_outbox.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        self.store_a = self.store_type(
            POSTGRES_URL, authorize_operator=lambda value: value == "operator",
            base_retry_seconds=1, max_retry_seconds=4, max_attempts=2, lease_seconds=5,
        )
        self.store_b = self.store_type(
            POSTGRES_URL, authorize_operator=lambda value: value == "operator",
            base_retry_seconds=1, max_retry_seconds=4, max_attempts=2, lease_seconds=5,
        )
        self.alert_ids = []

    def tearDown(self):
        if self.alert_ids:
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute("DELETE FROM security_alert_outbox WHERE alert_id = ANY(%s)", (self.alert_ids,))
        self.store_a.close()
        self.store_b.close()

    def incident(self):
        incident_id = uuid.uuid4().hex
        return {
            "incident_id": incident_id,
            "category": "audit_integrity_failure",
            "severity": "critical",
            "status": "open",
            "first_seen": 1000.0,
            "last_seen": 1000.0,
            "event_count": 1,
            "last_reason_code": "audit_chain_invalid",
        }

    def enqueue(self):
        incident = self.incident()
        alert_id = self.store_a.enqueue_incident(incident)
        self.alert_ids.append(alert_id)
        return alert_id

    def test_concurrent_independent_pools_produce_exactly_one_claim(self):
        alert_id = self.enqueue()
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def claim(store, worker_id):
            try:
                barrier.wait(timeout=5)
                results.append(store.claim(worker_id))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=claim, args=(self.store_a, "worker_a")),
            threading.Thread(target=claim, args=(self.store_b, "worker_b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["alert_id"], alert_id)

    def test_payload_change_with_same_incident_version_fields_creates_new_alert(self):
        incident = self.incident()
        first = self.store_a.enqueue_incident(incident)
        changed = dict(incident, last_reason_code="audit_anchor_missing")
        second = self.store_b.enqueue_incident(changed)
        self.alert_ids.extend([first, second])
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.store_a.list_alerts("operator")), 2)

    def test_enqueue_is_idempotent_and_payload_is_minimized(self):
        incident = self.incident()
        first = self.store_a.enqueue_incident(incident)
        second = self.store_b.enqueue_incident(incident)
        self.alert_ids.append(first)
        self.assertEqual(first, second)
        item = self.store_a.claim("worker_a")
        self.assertEqual(item["alert_id"], first)
        self.assertEqual(set(item["payload"]), {
            "incident_id", "category", "severity", "status",
            "first_seen", "last_seen", "event_count", "last_reason_code",
        })

    def test_only_one_independent_pool_claims_and_stale_worker_cannot_ack(self):
        alert_id = self.enqueue()
        stale = self.store_a.claim("worker_a")
        self.assertEqual(stale["alert_id"], alert_id)
        self.assertIsNone(self.store_b.claim("worker_b"))
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE security_alert_outbox SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE alert_id=%s", (alert_id,),
            )
        current = self.store_b.claim("worker_b")
        self.assertEqual(current["attempt"], 2)
        with self.assertRaises(PermissionError):
            self.store_a.complete("worker_a", alert_id, lease_token=stale["lease_token"], lease_epoch=stale["lease_epoch"], success=True)
        self.assertEqual(
            self.store_b.complete("worker_b", alert_id, lease_token=current["lease_token"], lease_epoch=current["lease_epoch"], success=True),
            "delivered",
        )

    def test_retry_backoff_and_dead_letter(self):
        alert_id = self.enqueue()
        first = self.store_a.claim("worker_a")
        self.assertEqual(
            self.store_a.complete("worker_a", alert_id, lease_token=first["lease_token"], lease_epoch=first["lease_epoch"],
                                  success=False, error_code="provider_error"),
            "pending",
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE security_alert_outbox SET available_at=clock_timestamp()-interval '1 second' "
                "WHERE alert_id=%s", (alert_id,),
            )
        second = self.store_b.claim("worker_b")
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(
            self.store_b.complete("worker_b", alert_id, lease_token=second["lease_token"], lease_epoch=second["lease_epoch"],
                                  success=False, error_code="provider_error"),
            "dead",
        )
        self.assertEqual(self.store_a.list_alerts("operator", state="dead")[0]["alert_id"], alert_id)

    def test_expired_worker_crashes_are_bounded_and_dead_lettered(self):
        alert_id = self.enqueue()
        first = self.store_a.claim("crashing_worker")
        self.assertEqual(first["attempt"], 1)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE security_alert_outbox SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE alert_id=%s", (alert_id,),
            )
        second = self.store_b.claim("crashing_worker")
        self.assertEqual(second["attempt"], 2)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE security_alert_outbox SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE alert_id=%s", (alert_id,),
            )
        self.assertIsNone(self.store_a.claim("recovery_worker"))
        dead = self.store_b.list_alerts("operator", state="dead")
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0]["alert_id"], alert_id)
        self.assertEqual(dead[0]["last_error_code"], "lease_expired")

    def test_health_snapshot_is_authorized_and_payload_free(self):
        self.enqueue()
        snapshot = self.store_a.health_snapshot("operator")
        self.assertEqual(snapshot["pending"], 1)
        self.assertEqual(snapshot["inflight"], 0)
        self.assertEqual(snapshot["dead"], 0)
        self.assertIsNotNone(snapshot["oldest_pending_age_seconds"])
        self.assertNotIn("payload", snapshot)
        with self.assertRaises(PermissionError):
            self.store_a.health_snapshot("attacker")

    def test_operator_authorization_is_required(self):
        self.enqueue()
        with self.assertRaises(PermissionError):
            self.store_a.list_alerts("attacker")


if __name__ == "__main__":
    unittest.main()
