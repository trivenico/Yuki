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
        migrations = [
            Path(__file__).resolve().parents[1] / "migrations/security/0001_security_work_leases.sql",
            Path(__file__).resolve().parents[1] / "migrations/security/0002_security_ingestion_cursors.sql",
        ]
        with psycopg.connect(POSTGRES_URL) as conn:
            for migration in migrations:
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

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


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresSecurityIngestionCursorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.postgres_security_event_ingestion import PostgresSecurityEventIngestionCursors
        cls.store_type = PostgresSecurityEventIngestionCursors
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0002_security_ingestion_cursors.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        # Use a per-test cursor key: the CI PostgreSQL database is shared by
        # several integration suites and must not inherit another test's epoch.
        self.source = "authorization_audit_" + uuid.uuid4().hex
        self.store_a = self.store_type(POSTGRES_URL, max_pool_size=2, lease_seconds=5)
        self.store_b = self.store_type(POSTGRES_URL, max_pool_size=2, lease_seconds=5)

    def tearDown(self):
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("DELETE FROM security_ingestion_cursors WHERE source=%s", (self.source,))
        self.store_a.close()
        self.store_b.close()

    def test_claim_is_exclusive_across_independent_pools(self):
        first = self.store_a.claim(self.source, "worker_a")
        self.assertIsNotNone(first)
        self.assertIsNone(self.store_b.claim(self.source, "worker_b"))
        self.assertEqual(first["lease_epoch"], 1)

    def test_stale_claim_cannot_complete_or_fail_after_replacement(self):
        stale = self.store_a.claim(self.source, "worker_a")
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE security_ingestion_cursors SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE source=%s", (self.source,)
            )
        current = self.store_b.claim(self.source, "worker_b")
        self.assertEqual(current["lease_epoch"], stale["lease_epoch"] + 1)
        with self.assertRaises(PermissionError):
            self.store_a.complete(stale, "worker_a", 1)
        with self.assertRaises(PermissionError):
            self.store_a.fail(stale, "worker_a", "late_failure")
        self.assertEqual(self.store_b.complete(current, "worker_b", 4), 4)
        self.assertEqual(self.store_b.snapshot()[self.source]["event_id"], 4)

    def test_failure_releases_only_current_unexpired_lease(self):
        claim = self.store_a.claim(self.source, "worker_a")
        self.store_a.fail(claim, "worker_a", "source_unavailable")
        snapshot = self.store_a.snapshot()[self.source]
        self.assertEqual(snapshot["last_error_code"], "source_unavailable")
        self.assertFalse(snapshot["lease_active"])

    def test_source_outage_releases_cursor_and_retry_resumes_without_skipping(self):
        from core.security_event_ingestion import DurableSecurityEventIngestor

        class FlakyMonitor:
            attempts = 0

            def ingest_authorization_audit(self, audit_log, *, after_event_id, limit):
                self.attempts += 1
                if self.attempts == 1:
                    raise ConnectionError("simulated source outage")
                self.asserted_cursor = after_event_id
                return after_event_id + 3

        monitor = FlakyMonitor()
        ingestor = DurableSecurityEventIngestor(monitor, self.store_a)
        with self.assertRaises(ConnectionError):
            ingestor.ingest_authorization_page(object(), "recovery_worker")
        failed = self.store_a.snapshot()[self.source]
        self.assertEqual(failed["event_id"], 0)
        self.assertEqual(failed["last_error_code"], "source_ingestion_failed")
        self.assertFalse(failed["lease_active"])

        recovered = ingestor.ingest_authorization_page(object(), "recovery_worker")
        self.assertEqual(recovered, {"status": "ok", "source": self.source, "cursor": 3})
        self.assertEqual(monitor.asserted_cursor, 0)
        self.assertEqual(self.store_a.snapshot()[self.source]["event_id"], 3)

    def test_monitor_write_before_cursor_commit_replays_idempotently(self):
        from core.security_event_ingestion import DurableSecurityEventIngestor

        class IdempotentMonitor:
            def __init__(self):
                self.processed = set()
                self.calls = 0

            def ingest_authorization_audit(self, audit_log, *, after_event_id, limit):
                self.calls += 1
                self.processed.update((after_event_id + 1, after_event_id + 2))
                return after_event_id + 2

        monitor = IdempotentMonitor()
        ingestor = DurableSecurityEventIngestor(monitor, self.store_a)
        original_complete = self.store_a.complete
        fail_once = {"value": True}

        def crash_before_cursor_commit(claim, worker_id, event_id):
            if fail_once["value"]:
                fail_once["value"] = False
                raise ConnectionError("simulated worker death before cursor commit")
            return original_complete(claim, worker_id, event_id)

        self.store_a.complete = crash_before_cursor_commit
        with self.assertRaises(ConnectionError):
            ingestor.ingest_authorization_page(object(), "replay_worker")
        self.assertEqual(self.store_a.snapshot()[self.source]["event_id"], 0)
        self.store_a.complete = original_complete

        result = ingestor.ingest_authorization_page(object(), "replay_worker")
        self.assertEqual(result["cursor"], 2)
        self.assertEqual(monitor.calls, 2)
        self.assertEqual(monitor.processed, {1, 2})

    def test_repository_is_accepted_by_durable_ingestor_contract(self):
        from core.security_event_ingestion import DurableSecurityEventIngestor

        class Monitor:
            def ingest_authorization_audit(self, audit_log, *, after_event_id, limit):
                return after_event_id + 2

        ingestor = DurableSecurityEventIngestor(Monitor(), self.store_a)
        result = ingestor.ingest_authorization_page(object(), "worker_a")
        self.assertEqual(result, {"status": "ok", "source": self.source, "cursor": 2})


if __name__ == "__main__":
    unittest.main()
