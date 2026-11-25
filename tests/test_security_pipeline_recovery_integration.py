"""Cross-component recovery tests for the durable security operations pipeline."""
import os
import sqlite3
import tempfile
import unittest

from core.security_alert_outbox import SecurityAlertOutbox
from core.security_event_ingestion import (
    DurableSecurityEventIngestor,
    SecurityEventIngestionCursors,
)
from core.security_event_monitor import SecurityEventMonitor
from core.security_operations_health import (
    SecurityOperationsHealth,
    SecurityWorkerHeartbeat,
)
from core.security_pipeline_workers import SecurityPipelineWorker


class FailOnceAfterImportCursors(SecurityEventIngestionCursors):
    """Simulate a process interruption after monitor commit but before cursor commit."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_complete_once = True

    def complete(self, claim, worker_id, event_id):
        if self.fail_complete_once:
            self.fail_complete_once = False
            raise RuntimeError("simulated cursor commit interruption")
        return super().complete(claim, worker_id, event_id)


class SQLiteAuditSource:
    """Expose the repository page contract over the test SQLite fixture."""

    def __init__(self, database_path):
        self.database_path = database_path

    def event_page(self, *, after_event_id=0, limit=500):
        with sqlite3.connect(self.database_path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT event_id, occurred_at, principal_hash, reason_code, allowed
                   FROM authorization_audit_events
                   WHERE event_id > ? ORDER BY event_id LIMIT ?""",
                (after_event_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]


class SecurityPipelineRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 10_000.0
        root = self.tmp.name
        self.monitor = SecurityEventMonitor(
            os.path.join(root, "monitor.sqlite"),
            authorize_operator=lambda value: value == "ops",
            failure_threshold=2,
            clock=lambda: self.now,
        )
        self.cursors = FailOnceAfterImportCursors(
            os.path.join(root, "cursors.sqlite"),
            clock=lambda: self.now,
            lease_seconds=10,
        )
        self.source_path = os.path.join(root, "authorization.sqlite")
        with sqlite3.connect(self.source_path) as db:
            db.execute("""CREATE TABLE authorization_audit_events(
                event_id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL,
                principal_hash TEXT NOT NULL, reason_code TEXT NOT NULL,
                allowed INTEGER NOT NULL)""")
            db.execute(
                "INSERT INTO authorization_audit_events VALUES(1, ?, ?, ?, 0)",
                ("2026-11-24T10:00:00+00:00", "a" * 64, "policy_denied"),
            )
        self.source = SQLiteAuditSource(self.source_path)
        self.ingestor = DurableSecurityEventIngestor(
            self.monitor, self.cursors, batch_size=10
        )
        self.outbox = SecurityAlertOutbox(
            os.path.join(root, "outbox.sqlite"),
            authorize_operator=lambda value: value == "ops",
            clock=lambda: self.now,
            base_retry_seconds=5,
            max_retry_seconds=20,
            max_attempts=2,
            lease_seconds=10,
        )
        self.heartbeats = SecurityWorkerHeartbeat(
            os.path.join(root, "heartbeats.sqlite"), clock=lambda: self.now
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_replay_after_monitor_commit_before_cursor_commit_is_idempotent(self):
        with self.assertRaises(RuntimeError):
            self.ingestor.ingest_authorization_page(self.source, "ingest-1")

        # The monitor committed the signal, but the durable cursor did not move.
        self.assertEqual(self.cursors.snapshot()["authorization_audit"]["event_id"], 0)
        with sqlite3.connect(self.monitor.database_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM security_events").fetchone()[0], 1)

        recovered = self.ingestor.ingest_authorization_page(self.source, "ingest-2")
        self.assertEqual(recovered["cursor"], 1)
        with sqlite3.connect(self.monitor.database_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM security_events").fetchone()[0], 1)

    def test_worker_failure_does_not_beat_and_next_success_recovers(self):
        calls = {"count": 0}

        def operation():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("sensitive source endpoint detail")
            return self.ingestor.ingest_authorization_page(self.source, "ingest-worker")

        worker = SecurityPipelineWorker(
            "ingestion", "ingest-worker", operation, self.heartbeats,
            base_retry_seconds=2, max_retry_seconds=8, clock=lambda: self.now,
        )
        failed = worker.run_once()
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("sensitive source endpoint detail", str(failed))
        self.assertNotIn("ingestion", self.heartbeats.snapshot())

        # The interrupted ingestion still owns its cursor lease. Recovery
        # must wait for that lease to expire before another worker can claim it.
        self.now += 11
        recovered = worker.run_once()
        self.assertEqual(recovered["status"], "ok")
        self.assertEqual(self.heartbeats.snapshot()["ingestion"], self.now)
        self.assertEqual(worker.consecutive_failures, 0)

    def test_health_moves_from_unhealthy_to_healthy_after_cursor_and_worker_recovery(self):
        health = SecurityOperationsHealth(
            self.monitor, self.outbox, self.heartbeats,
            authorize_operator=lambda value: value == "ops",
            ingestion_cursors=self.cursors,
            required_ingestion_sources=("authorization_audit",),
            clock=lambda: self.now,
        )
        self.assertEqual(health.snapshot("ops")["status"], "unhealthy")

        # This scenario tests health reporting, not the one-shot crash injected
        # by the replay test's cursor fake.
        self.cursors.fail_complete_once = False
        claim = self.cursors.claim("authorization_audit", "ingest-worker")
        self.cursors.complete(claim, "ingest-worker", 0)
        for role in self.heartbeats.ROLES:
            self.heartbeats.beat(role, role + "-worker")
        self.assertEqual(health.snapshot("ops")["status"], "healthy")

        self.now += 301
        self.assertFalse(health.snapshot("ops")["checks"]["ingestion_cursors"])

    def test_outbox_crash_recovery_dead_letter_is_reflected_in_health(self):
        incident = {
            "incident_id": "b" * 32,
            "category": "audit_integrity_failure",
            "severity": "critical",
            "status": "open",
            "first_seen": self.now,
            "last_seen": self.now,
            "event_count": 1,
            "last_reason_code": "audit_chain_invalid",
        }
        self.outbox.enqueue_incident(incident)
        first = self.outbox.claim("dispatch-a")
        self.assertEqual(first["attempt"], 1)
        self.now += 11
        second = self.outbox.claim("dispatch-b")
        self.assertEqual(second["attempt"], 2)
        self.now += 11
        self.assertIsNone(self.outbox.claim("dispatch-recovery"))

        snapshot = self.outbox.list_alerts("ops", state="dead")
        self.assertEqual(len(snapshot), 1)
        health = SecurityOperationsHealth(
            self.monitor, self.outbox, self.heartbeats,
            authorize_operator=lambda value: value == "ops",
            clock=lambda: self.now,
        )
        result = health.snapshot("ops")
        self.assertEqual(result["outbox"]["dead"], 1)
        self.assertFalse(result["checks"]["dead_letters"])
        self.assertEqual(result["status"], "unhealthy")


if __name__ == "__main__":
    unittest.main()
