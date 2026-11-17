import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.workflow_store import (
    WorkflowConflict, WorkflowRunStore, WorkflowTransitionError,
)


class WorkflowRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "workflows.sqlite3"))
        self.definition = [
            {"name": "review", "action": "emit", "argument": "check this", "requires_approval": True}
        ]

    def create(self, key="workflow-1"):
        return self.store.create(
            idempotency_key=key, workflow_name="release-check", definition=self.definition
        )

    def test_legacy_schema_upgrade_adds_approval_columns_atomically(self):
        import sqlite3

        path = Path(self.tmp.name) / "legacy-workflows.sqlite3"
        db = sqlite3.connect(path)
        db.execute("""CREATE TABLE workflow_runs (
            run_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            request_digest TEXT NOT NULL,
            workflow_name TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            state TEXT NOT NULL,
            result_json TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at TEXT
        )""")
        db.commit()
        db.close()

        upgraded = WorkflowRunStore(str(path))
        with upgraded._connect() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(workflow_runs)")}
        self.assertTrue({
            "approved_by", "approved_at", "approval_digest", "approved_session_hash",
        }.issubset(columns))
        created = upgraded.create(
            idempotency_key="legacy-upgrade", workflow_name="post-upgrade",
            definition=self.definition,
        )
        self.assertEqual(created.state, "queued")

    def test_create_and_idempotent_retry(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.state, "queued")
        self.assertEqual(first.version, 0)
        self.assertEqual(first.definition, self.definition)

    def test_concurrent_idempotent_creates_return_same_run(self):
        results = []
        errors = []
        barrier = threading.Barrier(6)

        def create():
            try:
                barrier.wait(timeout=3)
                results.append(self.create(key="concurrent-key").run_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        self.assertEqual(len(set(results)), 1)

    def test_claim_next_is_atomic_across_workers(self):
        queued = self.create(key="single-claim")
        queued = self.store.approve(queued.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=queued.version)
        barrier = threading.Barrier(8)
        claims = []
        errors = []

        def claim(worker):
            try:
                barrier.wait(timeout=3)
                claims.append(self.store.claim_next(worker_id=worker))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=claim, args=(f"worker-{i}",)) for i in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(errors, [])
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].run_id, queued.run_id)
        self.assertEqual(winners[0].state, "running")
        self.assertEqual(winners[0].attempts, 1)

    def test_session_revocation_cancels_approved_queued_and_running_tasks(self):
        queued = self.create(key="revoke-queued")
        queued = self.store.approve(
            queued.run_id, actor_id="reviewer", session_id="logout-session",
            expected_version=queued.version,
        )
        running = self.create(key="revoke-running")
        running = self.store.approve(
            running.run_id, actor_id="reviewer", session_id="logout-session",
            expected_version=running.version,
        )
        running = self.store.transition(
            running.run_id, expected_version=running.version,
            to_state="running", lease_owner="worker-a",
        )
        cancelled = self.store.cancel_session_approved_runs("logout-session")
        self.assertEqual({run.run_id for run in cancelled}, {queued.run_id, running.run_id})
        for run in cancelled:
            self.assertEqual(run.state, "cancelled")
            self.assertEqual(run.error_code, "session_revoked")
            self.assertIsNone(run.lease_owner)
            events = self.store.list_task_events(run.run_id)
            self.assertEqual(events[-1].kind, "task.cancelled")
            self.assertEqual(events[-1].payload["reason"], "session_revoked")
        self.assertIsNone(self.store.claim_next(worker_id="worker-b"))

    def test_session_revocation_does_not_cancel_another_session(self):
        first = self.create(key="revoke-own")
        first = self.store.approve(first.run_id, actor_id="reviewer", session_id="session-own",
                                   expected_version=first.version)
        second = self.create(key="revoke-other")
        second = self.store.approve(second.run_id, actor_id="reviewer", session_id="session-other",
                                    expected_version=second.version)
        cancelled = self.store.cancel_session_approved_runs("session-own")
        self.assertEqual([run.run_id for run in cancelled], [first.run_id])
        self.assertEqual(self.store.get(second.run_id).state, "queued")

    def test_lease_renewal_is_owner_and_version_bound(self):
        queued = self.create(key="renew")
        queued = self.store.approve(queued.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=queued.version)
        claimed = self.store.claim_next(worker_id="worker-a", lease_seconds=30)
        self.assertEqual(claimed.run_id, queued.run_id)
        renewed = self.store.renew_lease(
            claimed.run_id, worker_id="worker-a", expected_version=claimed.version,
            lease_seconds=60,
        )
        self.assertEqual(renewed.version, claimed.version + 1)
        self.assertGreater(
            datetime.fromisoformat(renewed.lease_expires_at),
            datetime.fromisoformat(claimed.lease_expires_at),
        )
        with self.assertRaises(WorkflowTransitionError):
            self.store.renew_lease(
                renewed.run_id, worker_id="worker-b", expected_version=renewed.version
            )
        with self.assertRaises(WorkflowConflict):
            self.store.renew_lease(
                renewed.run_id, worker_id="worker-a", expected_version=claimed.version
            )

    def test_expired_lease_cannot_be_renewed(self):
        queued = self.create(key="expired-renew")
        queued = self.store.approve(queued.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=queued.version)
        claimed = self.store.claim_next(worker_id="worker-a", lease_seconds=1)
        self.assertEqual(claimed.run_id, queued.run_id)
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(), claimed.run_id),
            )
        with self.assertRaises(WorkflowConflict):
            self.store.renew_lease(
                claimed.run_id, worker_id="worker-a", expected_version=claimed.version
            )

    def test_protected_workflow_cannot_be_claimed_without_approval(self):
        run = self.create(key="approval-required")
        self.assertIsNone(self.store.claim_next(worker_id="worker-a"))
        with self.assertRaises(WorkflowTransitionError):
            self.store.transition(run.run_id, expected_version=run.version, to_state="running", lease_owner="worker-a")
        self.assertEqual(self.store.get(run.run_id).state, "queued")

    def test_approval_is_versioned_and_allows_claim(self):
        run = self.create(key="approval-versioned")
        approved = self.store.approve(run.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=run.version)
        self.assertEqual(approved.version, run.version + 1)
        self.assertEqual(approved.approved_by, "reviewer-1")
        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertEqual(claimed.run_id, run.run_id)
        self.assertEqual(claimed.state, "running")

    def test_stale_approval_version_is_rejected(self):
        run = self.create(key="approval-stale")
        with self.assertRaises(WorkflowConflict):
            self.store.approve(run.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=run.version + 1)

    def test_approval_requires_a_nonempty_actor(self):
        run = self.create(key="approval-actor")
        with self.assertRaises(ValueError):
            self.store.approve(run.run_id, actor_id=" ", session_id="session-1", expected_version=run.version)
    def test_idempotency_payload_mismatch_is_rejected(self):
        self.create()
        with self.assertRaises(WorkflowConflict):
            self.store.create(
                idempotency_key="workflow-1",
                workflow_name="other",
                definition=self.definition,
            )

    def test_state_transitions_are_versioned(self):
        queued = self.create()
        queued = self.store.approve(queued.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=queued.version)
        running = self.store.transition(
            queued.run_id, expected_version=queued.version, to_state="running", lease_owner="worker-a"
        )
        self.assertEqual(running.state, "running")
        self.assertEqual(running.version, 2)
        self.assertEqual(running.attempts, 1)
        succeeded = self.store.transition(
            running.run_id, expected_version=running.version, to_state="succeeded",
            result={"steps_completed": 1},
        )
        self.assertEqual(succeeded.state, "succeeded")
        self.assertEqual(succeeded.result, {"steps_completed": 1})
        self.assertIsNone(succeeded.lease_owner)

    def test_stale_version_and_illegal_transition_fail(self):
        run = self.create()
        with self.assertRaises(WorkflowConflict):
            self.store.transition(run.run_id, expected_version=4, to_state="running", lease_owner="w")
        with self.assertRaises(WorkflowTransitionError):
            self.store.transition(run.run_id, expected_version=0, to_state="succeeded")

    def test_running_requires_owner_and_bounded_lease(self):
        run = self.create()
        with self.assertRaises(ValueError):
            self.store.transition(run.run_id, expected_version=0, to_state="running")
        with self.assertRaises(ValueError):
            self.store.transition(
                run.run_id, expected_version=0, to_state="running", lease_owner="worker", lease_seconds=0
            )

    def test_expired_lease_is_interrupted_not_replayed(self):
        run = self.create()
        run = self.store.approve(run.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=run.version)
        running = self.store.transition(
            run.run_id, expected_version=run.version, to_state="running",
            lease_owner="worker-a", lease_seconds=1,
        )
        expired = self.store.mark_expired_running_as_interrupted(
            now=datetime.now(timezone.utc) + timedelta(seconds=5)
        )
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].run_id, running.run_id)
        self.assertEqual(expired[0].state, "interrupted")
        self.assertEqual(expired[0].error_code, "lease_expired")
        self.assertIsNone(expired[0].lease_owner)
        self.assertEqual(self.store.get(run.run_id).state, "interrupted")
        events = self.store.list_task_events(run.run_id)
        self.assertEqual(events[-1].kind, "task.interrupted")
        self.assertEqual(events[-1].actor_id, "system:lease-recovery")
        self.assertEqual(events[-1].payload["reason"], "lease_expired")

    def test_lease_expiry_requires_timezone_aware_clock(self):
        with self.assertRaises(ValueError):
            self.store.mark_expired_running_as_interrupted(now=datetime.now())

    def test_definition_validation_happens_before_persistence(self):
        with self.assertRaises(ValueError):
            self.store.create(
                idempotency_key="invalid", workflow_name="bad",
                definition=[{"name": "x", "action": "emit"}, {"name": "x", "action": "emit"}],
            )
        self.assertEqual(self.store.list_runs(), [])

    def test_payload_and_result_limits(self):
        with self.assertRaises(ValueError):
            self.store.create(
                idempotency_key="oversized", workflow_name="x",
                definition=[{"name": "x", "action": "emit", "argument": "a" * 2100}],
            )
        run = self.create()
        run = self.store.approve(run.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=run.version)
        running = self.store.transition(
            run.run_id, expected_version=run.version, to_state="running", lease_owner="w"
        )
        with self.assertRaises(ValueError):
            self.store.transition(
                run.run_id, expected_version=running.version, to_state="succeeded",
                result={"huge": "x" * 20_000},
            )

    def test_list_state_and_limits(self):
        self.create()
        self.assertEqual(len(self.store.list_runs(state="queued")), 1)
        with self.assertRaises(ValueError):
            self.store.list_runs(limit=101)

    def test_connections_are_closed(self):
        run = self.create()
        self.store.get(run.run_id)
        import os
        os.remove(Path(self.tmp.name) / "workflows.sqlite3")
        self.assertFalse((Path(self.tmp.name) / "workflows.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
