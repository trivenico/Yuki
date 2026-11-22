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

    def create(self, key="workflow-1", *, owner_id="test-owner"):
        return self.store.create(
            idempotency_key=key, workflow_name="release-check",
            definition=self.definition, owner_id=owner_id,
        )

    def test_owner_scoped_reads_quarantine_legacy_unowned_rows(self):
        legacy = self.create("legacy-unowned", owner_id=None)
        alice = self.store.create(
            idempotency_key="owned-alice", workflow_name="alice",
            definition=self.definition, owner_id="alice",
        )
        bob = self.store.create(
            idempotency_key="owned-bob", workflow_name="bob",
            definition=self.definition, owner_id="bob",
        )

        self.assertIsNone(self.store.get_for_owner(legacy.run_id, owner_id="alice"))
        self.assertEqual(self.store.get_for_owner(alice.run_id, owner_id="alice").owner_id, "alice")
        self.assertIsNone(self.store.get_for_owner(alice.run_id, owner_id="bob"))
        self.assertEqual([run.run_id for run in self.store.list_runs_for_owner("alice")],
                         [alice.run_id])
        self.assertEqual([run.run_id for run in self.store.list_runs_for_owner("bob")],
                         [bob.run_id])

    def test_workflow_owner_is_immutable(self):
        owned = self.store.create(
            idempotency_key="immutable-owner", workflow_name="owned",
            definition=self.definition, owner_id="alice",
        )
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connect() as db:
                db.execute(
                    "UPDATE workflow_runs SET owner_id='bob' WHERE run_id=?",
                    (owned.run_id,),
                )
        self.assertEqual(self.store.get(owned.run_id).owner_id, "alice")

    def test_idempotency_key_is_scoped_to_owner(self):
        self.store.create(
            idempotency_key="owner-key", workflow_name="owned",
            definition=self.definition, owner_id="alice",
        )
        with self.assertRaises(WorkflowConflict):
            self.store.create(
                idempotency_key="owner-key", workflow_name="owned",
                definition=self.definition, owner_id="bob",
            )

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

    def test_legacy_unowned_queued_run_is_never_claimed(self):
        legacy = self.create("legacy-queued", owner_id=None)
        self.store.approve(
            legacy.run_id, actor_id="legacy-reviewer", session_id="legacy-session",
            expected_version=legacy.version,
        )
        self.assertIsNone(self.store.claim_next(worker_id="worker-a"))
        self.assertEqual(self.store.get(legacy.run_id).state, "queued")

    def test_expired_legacy_unowned_runs_are_not_recovered(self):
        legacy = self.create("legacy-running", owner_id=None)
        # Simulate a running row created before owner_id was introduced. Seed
        # the persisted legacy state directly so this test exercises recovery
        # quarantine rather than the current approval/transition contract.
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET state='running', attempts=1, "
                "lease_owner='legacy-worker', lease_expires_at=? WHERE run_id=?",
                (expired_at, legacy.run_id),
            )
        self.assertEqual(self.store.recover_expired_with_events(actor_id="worker-service"), [])
        self.assertEqual(self.store.get(legacy.run_id).state, "running")
        self.assertEqual(
            self.store.mark_expired_running_as_interrupted(
                now=datetime.now(timezone.utc) + timedelta(seconds=10)
            ),
            [],
        )
        self.assertEqual(self.store.get(legacy.run_id).state, "running")

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
