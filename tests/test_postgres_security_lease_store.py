"""Integration tests for PostgreSQL security lease claims.

Set YUKI_TEST_POSTGRES_URL to a disposable PostgreSQL database to enable these
tests. They intentionally skip rather than substituting SQLite semantics.
"""
import os
from pathlib import Path
import threading
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover - dependency is in production requirements
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresSecurityLeaseStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.postgres_security_lease_store import PostgresSecurityLeaseStore
        cls.store_type = PostgresSecurityLeaseStore
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0001_security_work_leases.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"))

    def setUp(self):
        self.queue = "test_" + uuid.uuid4().hex[:20]
        self.resource = "resource-" + uuid.uuid4().hex
        self.store_a = self.store_type(POSTGRES_URL, max_pool_size=2)
        self.store_b = self.store_type(POSTGRES_URL, max_pool_size=2)

    def tearDown(self):
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("DELETE FROM security_work_leases WHERE queue_name=%s", (self.queue,))
        self.store_a.close()
        self.store_b.close()

    def test_only_one_independent_connection_wins_claim(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def claim(store, worker):
            try:
                barrier.wait(timeout=5)
                results.append(store.claim(self.queue, self.resource, worker))
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
        self.assertEqual(errors, [])
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].lease_epoch, 1)

    def test_expired_claim_is_replaced_and_old_fence_is_rejected(self):
        old = self.store_a.claim(self.queue, self.resource, "worker_a")
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE security_work_leases SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE queue_name=%s AND resource_key=%s",
                (self.queue, self.resource),
            )
        new = self.store_b.claim(self.queue, self.resource, "worker_b")
        self.assertEqual(new.lease_epoch, old.lease_epoch + 1)
        self.assertIsNone(self.store_a.renew(old))
        self.assertFalse(self.store_a.release(old))
        self.assertIsNotNone(self.store_b.renew(new))

    def test_unexpired_lease_cannot_be_stolen(self):
        first = self.store_a.claim(self.queue, self.resource, "worker_a")
        self.assertIsNone(self.store_b.claim(self.queue, self.resource, "worker_b"))
        self.assertTrue(self.store_a.release(first))
        replacement = self.store_b.claim(self.queue, self.resource, "worker_b")
        self.assertEqual(replacement.lease_epoch, first.lease_epoch + 1)

    def test_invalid_lease_parameters_fail_before_database_access(self):
        for args in [
            ("bad queue", self.resource, "worker_a"),
            (self.queue, "bad resource with spaces", "worker_a"),
            (self.queue, self.resource, "worker with spaces"),
        ]:
            with self.assertRaises(ValueError):
                self.store_a.claim(*args)
        with self.assertRaises(ValueError):
            self.store_a.claim(self.queue, self.resource, "worker_a", lease_seconds=True)


if __name__ == "__main__":
    unittest.main()
