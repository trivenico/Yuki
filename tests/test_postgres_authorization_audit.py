"""Real PostgreSQL integration tests for the shared authorization audit chain."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresAuthorizationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.postgres_authorization_audit import PostgresAuthorizationAuditLog
        cls.audit_type = PostgresAuthorizationAuditLog
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0006_authorization_audit.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        self.audit_a = self.audit_type(POSTGRES_URL, max_pool_size=8)
        self.audit_b = self.audit_type(POSTGRES_URL, max_pool_size=8)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("TRUNCATE authorization_audit_events")
            conn.execute(
                "UPDATE authorization_audit_state SET last_event_id=0, head_hash=repeat('0', 64) WHERE singleton=1"
            )

    def tearDown(self):
        self.audit_a.close()
        self.audit_b.close()

    @staticmethod
    def event(index):
        from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
        request = AuthorizationRequest(
            Principal("worker-" + str(index), "workload", tenant_id="tenant-1", agent_id="agent-1"),
            "task:read", "resource:" + str(index), risk="high",
            task_id="task-" + str(index), expected_policy_revision="revision-1",
        )
        decision = AuthorizationDecision(index % 2 == 0, "decision-" + str(index), "revision-1",
                                          "allowed" if index % 2 == 0 else "denied")
        return request, decision

    def test_independent_pools_append_one_contiguous_valid_chain(self):
        barrier = threading.Barrier(8)
        def append(index):
            barrier.wait(timeout=10)
            request, decision = self.event(index)
            writer = self.audit_a if index % 2 else self.audit_b
            return writer(request, decision)
        with ThreadPoolExecutor(max_workers=8) as pool:
            event_ids = list(pool.map(append, range(8)))
        self.assertEqual(sorted(event_ids), list(range(1, 9)))
        self.assertTrue(self.audit_a.verify_chain())
        self.assertEqual(self.audit_a.head_hash(), self.audit_b.head_hash())
        self.assertEqual(self.audit_b.checkpoint().event_id, 8)

    def test_postgres_persisted_hash_matches_sqlite_for_fixed_event(self):
        import tempfile
        from datetime import datetime, timezone
        from unittest.mock import patch
        from core.authorization_audit import SQLiteAuthorizationAuditLog

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 2, 3, 4, 5, tzinfo=tz or timezone.utc)

        request, decision = self.event(42)
        with tempfile.TemporaryDirectory() as directory:
            sqlite_audit = SQLiteAuthorizationAuditLog(str(Path(directory) / "audit.sqlite3"))
            with patch("core.authorization_audit.datetime", FrozenDateTime), patch(
                "core.postgres_authorization_audit.datetime", FrozenDateTime
            ):
                sqlite_audit(request, decision)
                self.audit_a(request, decision)
            self.assertEqual(sqlite_audit.head_hash(), self.audit_a.head_hash())

    def test_event_page_is_bounded_ordered_and_contains_only_minimized_fields(self):
        request, decision = self.event(101)
        first_id = self.audit_a(request, decision)
        rows = self.audit_b.event_page(after_event_id=first_id - 1, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], first_id)
        self.assertEqual(set(rows[0]), {"event_id", "occurred_at", "principal_hash", "reason_code", "allowed"})
        self.assertEqual(rows[0]["allowed"], 0 if not decision.allowed else 1)

    def test_evidence_page_is_hash_bound_and_separate_from_minimized_projection(self):
        request, decision = self.event(102)
        event_id = self.audit_a(request, decision)
        minimized = self.audit_b.event_page(after_event_id=event_id - 1, limit=1)[0]
        evidence = self.audit_b.evidence_page(after_event_id=event_id - 1, limit=1)[0]
        self.assertEqual(set(minimized), {
            "event_id", "occurred_at", "principal_hash", "reason_code", "allowed",
        })
        self.assertTrue({
            "event_id", "action", "decision_hash", "policy_revision_hash",
            "previous_hash", "event_hash", "resource_hash",
        }.issubset(evidence))
        self.assertEqual(evidence["event_id"], event_id)
        self.assertEqual(evidence["allowed"], decision.allowed)
        self.assertEqual(evidence["event_hash"], self.audit_b.checkpoint().event_hash)

    def test_checkpoint_publication_fails_closed_when_anchor_is_uninitialized(self):
        class MemoryAnchor:
            def __init__(self):
                self.value = None
            def latest(self):
                return self.value
            def publish(self, checkpoint):
                self.value = checkpoint

        anchor = MemoryAnchor()
        with self.assertRaisesRegex(RuntimeError, "requires_operator_bootstrap"):
            self.audit_a.publish_checkpoint(anchor)
        self.assertIsNone(anchor.latest())

    def test_anchor_verification_detects_mutation(self):
        from core.authorization_audit import AuthorizationAuditCheckpoint
        class MemoryAnchor:
            def __init__(self):
                self.value = None
            def publish(self, checkpoint):
                if self.value and checkpoint.event_id < self.value.event_id:
                    raise ValueError("checkpoint_rollback")
                self.value = checkpoint
            def latest(self):
                return self.value
        request, decision = self.event(7)
        self.audit_a(request, decision)
        anchor = MemoryAnchor()
        with self.assertRaisesRegex(RuntimeError, "requires_operator_bootstrap"):
            self.audit_a.publish_checkpoint(anchor)
        anchor.publish(self.audit_a.checkpoint())  # Explicit operator bootstrap.
        self.audit_a.publish_checkpoint(anchor)
        self.assertTrue(self.audit_b.verify_anchor(anchor))
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("ALTER TABLE authorization_audit_events DISABLE TRIGGER authorization_audit_events_no_update")
            conn.execute("UPDATE authorization_audit_events SET reason_code='tampered' WHERE event_id=1")
            conn.execute("ALTER TABLE authorization_audit_events ENABLE TRIGGER authorization_audit_events_no_update")
        self.assertFalse(self.audit_b.verify_anchor(anchor))

    def make_sqlite_source(self, directory, *, count=3):
        from core.authorization_audit import SQLiteAuthorizationAuditLog
        path = Path(directory) / "source.sqlite3"
        source = SQLiteAuthorizationAuditLog(str(path))
        for index in range(count):
            request, decision = self.event(200 + index)
            source(request, decision)
        return source, path

    def test_import_preserves_exact_ids_hashes_and_anchor(self):
        import tempfile
        class MemoryAnchor:
            def __init__(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value
            def publish(self, checkpoint):
                if self.value and checkpoint.event_id < self.value.event_id:
                    raise ValueError("checkpoint_rollback")
                self.value = checkpoint

        with tempfile.TemporaryDirectory() as directory:
            source, path = self.make_sqlite_source(directory)
            anchor = MemoryAnchor(source.checkpoint())
            expected_hash = source.head_hash()
            self.assertEqual(self.audit_a.import_sqlite_history(str(path), anchor), 3)
            self.assertTrue(self.audit_b.verify_chain())
            self.assertTrue(self.audit_b.verify_anchor(anchor))
            self.assertEqual(self.audit_b.head_hash(), expected_hash)
            self.assertEqual([row["event_id"] for row in self.audit_b.event_page(limit=10)], [1, 2, 3])

    def test_import_rejects_source_that_does_not_match_anchor_without_writing(self):
        import tempfile
        from core.authorization_audit import AuthorizationAuditCheckpoint
        class MemoryAnchor:
            def latest(self):
                return AuthorizationAuditCheckpoint(1, "f" * 64, "2026-01-02T03:04:05+00:00")
            def publish(self, checkpoint):
                raise AssertionError("must_not_publish")
        with tempfile.TemporaryDirectory() as directory:
            source, path = self.make_sqlite_source(directory, count=2)
            with self.assertRaisesRegex(RuntimeError, "anchor_not_at_source_head"):
                self.audit_a.import_sqlite_history(str(path), MemoryAnchor())
            self.assertEqual(self.audit_a.checkpoint().event_id, 0)

    def test_import_rejects_tampered_source_without_writing(self):
        import tempfile
        import sqlite3
        class MemoryAnchor:
            def __init__(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value
            def publish(self, checkpoint):
                self.value = checkpoint
        with tempfile.TemporaryDirectory() as directory:
            source, path = self.make_sqlite_source(directory, count=2)
            anchor = MemoryAnchor(source.checkpoint())
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE authorization_audit_events SET reason_code='tampered' WHERE event_id=1")
            with self.assertRaisesRegex(RuntimeError, "source_event_hash_invalid"):
                self.audit_a.import_sqlite_history(str(path), anchor)
            self.assertEqual(self.audit_a.checkpoint().event_id, 0)

    def test_import_requires_empty_postgres_target(self):
        import tempfile
        class MemoryAnchor:
            def __init__(self, checkpoint):
                self.value = checkpoint
            def latest(self):
                return self.value
            def publish(self, checkpoint):
                self.value = checkpoint
        with tempfile.TemporaryDirectory() as directory:
            source, path = self.make_sqlite_source(directory, count=1)
            anchor = MemoryAnchor(source.checkpoint())
            request, decision = self.event(999)
            self.audit_a(request, decision)
            with self.assertRaisesRegex(RuntimeError, "target_not_empty"):
                self.audit_b.import_sqlite_history(str(path), anchor)

    def test_readiness_confirms_migrated_schema(self):
        other = self.audit_type(POSTGRES_URL)
        try:
            self.assertTrue(other.check_ready())
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
