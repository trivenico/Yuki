import hashlib
import tempfile
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.worker_registry import WorkerRegistry
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
        # Normal workflow fixtures represent account-owned tasks. Dedicated
        # legacy tests create owner_id=None explicitly to verify quarantine.
        return self.store.create(
            idempotency_key=key, workflow_name="release-check",
            definition=self.definition, owner_id="test-owner",
        )

    def test_retry_must_preserve_dependency_graph(self):
        dependency = self.create(key="retry-lineage-prerequisite")
        dependency = self.store.approve(
            dependency.run_id, actor_id="reviewer-1", session_id="dependency-session",
            expected_version=dependency.version,
        )
        dependency_claim = self.store.claim_next(worker_id="worker-dependency")
        self.assertEqual(dependency_claim.run_id, dependency.run_id)
        self.store.transition(
            dependency.run_id, expected_version=dependency_claim.version,
            to_state="succeeded", result={"ok": True},
        )

        source = self.store.create(
            idempotency_key="retry-lineage-source",
            workflow_name="release-check", definition=self.definition,
            owner_id="test-owner", dependencies=(dependency.run_id,),
        )
        source = self.store.approve(
            source.run_id, actor_id="reviewer-1", session_id="source-session",
            expected_version=source.version,
        )
        source_claim = self.store.claim_next(worker_id="worker-source")
        self.assertEqual(source_claim.run_id, source.run_id)
        failed = self.store.transition(
            source.run_id, expected_version=source_claim.version,
            to_state="failed", error_code="test_failure",
        )

        with self.assertRaises(WorkflowConflict):
            self.store.create(
                idempotency_key="retry-lineage-dropped-dependency",
                workflow_name="release-check", definition=self.definition,
                owner_id="test-owner", retry_of_task_id=source.run_id,
            )
        retried = self.store.create(
            idempotency_key="retry-lineage-preserved-dependency",
            workflow_name="release-check", definition=self.definition,
            owner_id="test-owner", dependencies=(dependency.run_id,),
            retry_of_task_id=source.run_id,
        )
        self.assertEqual(self.store.get_dependencies(retried.run_id), (dependency.run_id,))
        self.assertEqual(retried.state, "queued")
        self.assertEqual(failed.state, "failed")

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

    def test_replacement_worker_session_cannot_renew_or_complete_old_lease(self):
        registry = WorkerRegistry(self.store.database_path)
        original_session = registry.register("session-bound-worker")
        task = self.create(key="session-bound-task")
        task = self.store.approve(
            task.run_id, actor_id="reviewer-1", session_id="session-bound-approval",
            expected_version=task.version,
        )
        claimed = self.store.claim_next(
            worker_id=original_session.worker_id,
            worker_session=(original_session.worker_id, original_session.session_token),
        )
        self.assertEqual(claimed.run_id, task.run_id)

        replacement_session = registry.register("session-bound-worker")
        with self.assertRaises(WorkflowConflict):
            self.store.renew_lease(
                claimed.run_id, worker_id=claimed.lease_owner,
                expected_version=claimed.version,
                expected_worker_session=(
                    replacement_session.worker_id, replacement_session.session_token,
                ),
            )
        with self.assertRaises(WorkflowConflict):
            self.store.renew_lease(
                claimed.run_id, worker_id=claimed.lease_owner,
                expected_version=claimed.version,
            )
        with self.assertRaises(WorkflowConflict):
            self.store.transition_with_event(
                claimed.run_id, expected_version=claimed.version,
                to_state="succeeded", result={"ok": True},
                event_kind="task.completed", actor_id="worker-service",
                event_payload={"state_version": claimed.version + 2},
                expected_lease_owner=claimed.lease_owner,
                expected_worker_session=(
                    replacement_session.worker_id, replacement_session.session_token,
                ),
            )
        with self.assertRaises(WorkflowConflict):
            self.store.transition_with_event(
                claimed.run_id, expected_version=claimed.version,
                to_state="succeeded", result={"ok": True},
                event_kind="task.completed", actor_id="worker-service",
                event_payload={"state_version": claimed.version + 2},
                expected_lease_owner=claimed.lease_owner,
                expected_worker_session=(
                    original_session.worker_id, original_session.session_token,
                ),
            )
        with self.assertRaises(WorkflowConflict):
            self.store.append_task_event(
                claimed.run_id, kind="task.progress", actor_id="worker-service",
                payload={"message": "stale worker", "percent": 1},
                expected_version=claimed.version,
                expected_lease_owner=claimed.lease_owner,
                expected_worker_session=(
                    replacement_session.worker_id, replacement_session.session_token,
                ),
            )
        with self.assertRaises(WorkflowTransitionError):
            self.store.transition(
                claimed.run_id, expected_version=claimed.version,
                to_state="succeeded", result={"bypass": True},
            )
        self.assertEqual(self.store.get(task.run_id).state, "running")

    def test_current_worker_lease_check_rejects_replaced_process_session(self):
        registry = WorkerRegistry(self.store.database_path)
        original = registry.register("orphan-recovery-worker")
        task = self.create(key="orphan-recovery-session-fence")
        task = self.store.approve(
            task.run_id, actor_id="reviewer-1", session_id="orphan-recovery-approval",
            expected_version=task.version,
        )
        claimed = self.store.claim_next(
            worker_id=original.worker_id,
            worker_session=(original.worker_id, original.session_token),
        )
        self.assertTrue(self.store.has_current_worker_lease(
            claimed.run_id, original.worker_id,
            hashlib.sha256(b"test-owner").hexdigest(),
        ))
        self.assertFalse(self.store.has_current_worker_lease(
            claimed.run_id, original.worker_id, hashlib.sha256(b"another-owner").hexdigest(),
        ))
        registry.register(original.worker_id)
        self.assertFalse(self.store.has_current_worker_lease(
            claimed.run_id, original.worker_id,
            hashlib.sha256(b"test-owner").hexdigest(),
        ))

    def test_child_claim_is_fenced_by_exact_parent_session(self):
        registry = WorkerRegistry(self.store.database_path)
        parent = registry.register("parent-worker")
        child = registry.register(
            "child-worker",
            metadata={
                "role": "specialist_pool_child",
                "parent_worker_id": parent.worker_id,
                "parent_session_hash": hashlib.sha256(
                    parent.session_token.encode("utf-8")
                ).hexdigest(),
            },
        )
        first = self.create(key="child-claim-current-parent")
        first = self.store.approve(
            first.run_id, actor_id="reviewer-1", session_id="session-1",
            expected_version=first.version,
        )
        with self.assertRaises(WorkflowTransitionError):
            self.store.claim_next(worker_id=child.worker_id)
        claimed = self.store.claim_next(
            worker_id=child.worker_id,
            worker_session=(child.worker_id, child.session_token),
        )
        self.assertEqual(claimed.run_id, first.run_id)
        self.assertEqual(claimed.state, "running")

        second = self.create(key="child-claim-replaced-parent")
        second = self.store.approve(
            second.run_id, actor_id="reviewer-1", session_id="session-2",
            expected_version=second.version,
        )
        registry.register("parent-worker")
        with self.assertRaises(WorkflowConflict):
            self.store.transition_with_event(
                first.run_id, expected_version=claimed.version,
                to_state="succeeded", result={"ok": True},
                event_kind="task.completed", actor_id="worker-service",
                event_payload={"state_version": claimed.version + 2},
                expected_lease_owner=child.worker_id,
                expected_worker_session=(child.worker_id, child.session_token),
            )
        with self.assertRaises(WorkflowTransitionError):
            self.store.claim_next(
                worker_id=child.worker_id,
                worker_session=(child.worker_id, child.session_token),
            )
        self.assertEqual(self.store.get(second.run_id).state, "queued")

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


    def test_worker_session_is_fenced_inside_atomic_claim(self):
        database = self.store.database_path
        registry = WorkerRegistry(database)
        run = self.create(key="worker-session-fenced")
        run = self.store.approve(
            run.run_id, actor_id="reviewer-1", session_id="session-1",
            expected_version=run.version,
        )
        old = registry.register("worker-a")
        current = registry.register("worker-a")
        with self.assertRaises(WorkflowTransitionError):
            self.store.claim_next(
                worker_id="worker-a",
                worker_session=("worker-a", old.session_token),
            )
        self.assertEqual(self.store.get(run.run_id).state, "queued")
        claimed = self.store.claim_next(
            worker_id="worker-a",
            worker_session=("worker-a", current.session_token),
        )
        self.assertEqual(claimed.run_id, run.run_id)
        self.assertEqual(claimed.state, "running")

    def test_worker_session_identity_must_match_claim_identity(self):
        registry = WorkerRegistry(self.store.database_path)
        session = registry.register("worker-a")
        with self.assertRaises(WorkflowTransitionError):
            self.store.claim_next(
                worker_id="worker-b",
                worker_session=("worker-a", session.session_token),
            )

    def test_claim_rejects_malformed_or_nul_worker_sessions(self):
        with self.assertRaises(ValueError):
            self.store.claim_next(worker_id="worker-a", worker_session=["worker-a", "token"])
        with self.assertRaises(ValueError):
            self.store.claim_next(worker_id="worker-a", worker_session=("worker-a", "bad\x00token"))

    def test_registered_worker_lease_renewal_requires_current_session(self):
        registry = WorkerRegistry(self.store.database_path)
        session = registry.register("renew-worker")
        queued = self.create(key="registered-worker-renewal")
        queued = self.store.approve(
            queued.run_id, actor_id="reviewer-1", session_id="renew-session",
            expected_version=queued.version,
        )
        claimed = self.store.claim_next(
            worker_id=session.worker_id,
            worker_session=(session.worker_id, session.session_token),
        )
        self.assertEqual(claimed.run_id, queued.run_id)
        with self.assertRaises(WorkflowConflict):
            self.store.renew_lease(
                claimed.run_id, worker_id=session.worker_id,
                expected_version=claimed.version,
            )
        renewed = self.store.renew_lease(
            claimed.run_id, worker_id=session.worker_id,
            expected_version=claimed.version,
            expected_worker_session=(session.worker_id, session.session_token),
        )
        self.assertEqual(renewed.version, claimed.version + 1)
        self.assertEqual(renewed.lease_owner, session.worker_id)

    def test_lease_renewal_rejects_nul_identifiers(self):
        with self.assertRaises(ValueError):
            self.store.renew_lease("bad\x00run", worker_id="worker-a", expected_version=0)
        with self.assertRaises(ValueError):
            self.store.renew_lease("run-1", worker_id="bad\x00worker", expected_version=0)

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

    def test_expired_lease_recovery_wins_race_against_renewal(self):
        run = self.create(key="recovery-renewal-race")
        run = self.store.approve(
            run.run_id, actor_id="reviewer-1", session_id="race-approval",
            expected_version=run.version,
        )
        claimed = self.store.claim_next(worker_id="race-worker", lease_seconds=60)
        self.assertEqual(claimed.run_id, run.run_id)
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                (expired_at, run.run_id),
            )

        barrier = threading.Barrier(2)

        self.assertTrue(claimed.lease_owner)
        def renew():
            barrier.wait(timeout=5)
            return self.store.renew_lease(
                run.run_id, worker_id=claimed.lease_owner,
                expected_version=claimed.version, lease_seconds=60,
            )

        def recover():
            barrier.wait(timeout=5)
            return self.store.recover_expired_with_events(
                actor_id="recovery-service", limit=10,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            renewal = pool.submit(renew)
            recovery = pool.submit(recover)
            with self.assertRaises((WorkflowConflict, WorkflowTransitionError)):
                renewal.result(timeout=10)
            recovered = recovery.result(timeout=10)

        self.assertEqual([item.run_id for item in recovered], [run.run_id])
        current = self.store.get(run.run_id)
        self.assertEqual(current.state, "interrupted")
        self.assertIsNone(current.lease_owner)
        self.assertIsNone(current.lease_expires_at)
        events = self.store.list_task_events(run.run_id)
        self.assertEqual(
            sum(event.kind == "task.interrupted" for event in events), 1,
        )

    def test_event_recovery_fences_exact_expired_lease_snapshot(self):
        run = self.create()
        run = self.store.approve(
            run.run_id, actor_id="reviewer-1", session_id="session-1",
            expected_version=run.version,
        )
        self.store.transition(
            run.run_id, expected_version=run.version, to_state="running",
            lease_owner="worker-a", lease_seconds=60,
        )
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                (expired_at, run.run_id),
            )

        recovered = self.store.recover_expired_with_events(actor_id="recovery-service")
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].run_id, run.run_id)
        self.assertEqual(recovered[0].state, "interrupted")
        self.assertIsNone(recovered[0].lease_owner)
        self.assertIsNone(recovered[0].lease_expires_at)
        events = self.store.list_task_events(run.run_id)
        interrupted = [event for event in events if event.kind == "task.interrupted"]
        self.assertEqual(len(interrupted), 1)
        self.assertFalse(interrupted[0].payload["automatic_replay"])
        self.assertEqual(interrupted[0].payload["state_version"], recovered[0].version)

    def test_recovery_rolls_back_state_when_audit_event_insert_fails(self):
        run = self.create(key="recovery-audit-rollback")
        approved = self.store.approve(
            run.run_id, actor_id="reviewer-1", session_id="session-1",
            expected_version=run.version,
        )
        running = self.store.transition(
            run.run_id, expected_version=approved.version, to_state="running",
            lease_owner="worker-a", lease_seconds=60,
        )
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                (expired_at, run.run_id),
            )

        with patch.object(
            self.store, "_append_task_event_in_transaction",
            side_effect=RuntimeError("simulated audit storage failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated audit storage failure"):
                self.store.recover_expired_with_events(actor_id="recovery-service")

        persisted = self.store.get(run.run_id)
        self.assertEqual(persisted.state, "running")
        self.assertEqual(persisted.lease_owner, "worker-a")
        self.assertEqual(persisted.version, running.version)
        self.assertFalse(any(
            event.kind == "task.interrupted"
            for event in self.store.list_task_events(run.run_id)
        ))

    def test_recovery_and_lease_renewal_race_is_serialized_across_store_instances(self):
        run = self.create(key="recovery-renewal-race")
        approved = self.store.approve(
            run.run_id, actor_id="reviewer-1", session_id="session-1",
            expected_version=run.version,
        )
        claimed = self.store.claim_next(worker_id="race-worker", lease_seconds=60)
        self.assertEqual(claimed.run_id, approved.run_id)
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                (expired_at, run.run_id),
            )

        recovery_store = WorkflowRunStore(self.store.database_path)
        renewal_store = WorkflowRunStore(self.store.database_path)
        barrier = threading.Barrier(2)

        def recover():
            barrier.wait(timeout=5)
            return recovery_store.recover_expired_with_events(actor_id="recovery-service")

        def renew():
            barrier.wait(timeout=5)
            try:
                renewal_store.renew_lease(
                    run.run_id, worker_id="race-worker",
                    expected_version=claimed.version, lease_seconds=60,
                )
                return "renewed"
            except (WorkflowConflict, WorkflowTransitionError):
                return "renewal_rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            recovery_future = pool.submit(recover)
            renewal_future = pool.submit(renew)
            recovered = recovery_future.result(timeout=10)
            renewal_outcome = renewal_future.result(timeout=10)

        final = self.store.get(run.run_id)
        self.assertEqual(final.state, "interrupted")
        self.assertIsNone(final.lease_owner)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(renewal_outcome, "renewal_rejected")
        interrupted = [
            event for event in self.store.list_task_events(run.run_id)
            if event.kind == "task.interrupted"
        ]
        self.assertEqual(len(interrupted), 1)

    def test_lease_expiry_requires_timezone_aware_clock(self):
        with self.assertRaises(ValueError):
            self.store.mark_expired_running_as_interrupted(now=datetime.now())

    def test_dependencies_never_cross_account_or_legacy_owner_boundary(self):
        alice = self.store.create(
            idempotency_key="alice-prerequisite", workflow_name="release-check",
            definition=self.definition, owner_id="alice",
        )
        bob = self.store.create(
            idempotency_key="bob-dependent", workflow_name="release-check",
            definition=self.definition, owner_id="bob",
        )
        with self.assertRaises(WorkflowConflict):
            self.store.create(
                idempotency_key="bob-cross-account-edge", workflow_name="release-check",
                definition=self.definition, owner_id="bob",
                dependencies=(alice.run_id,),
            )
        with self.assertRaises(WorkflowConflict):
            self.store.create(
                idempotency_key="legacy-cross-account-edge", workflow_name="release-check",
                definition=self.definition, dependencies=(alice.run_id,),
            )
        # Same-owner edges remain valid.
        same_owner = self.store.create(
            idempotency_key="alice-dependent", workflow_name="release-check",
            definition=self.definition, owner_id="alice",
            dependencies=(alice.run_id,),
        )
        self.assertEqual(self.store.get_dependencies(same_owner.run_id), (alice.run_id,))
        self.assertEqual(self.store.get(bob.run_id).owner_id, "bob")

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
