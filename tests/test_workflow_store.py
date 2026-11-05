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

    def start_worker_run(self, key, worker_id="worker-a"):
        run = self.create(key=key)
        approved = self.store.approve(
            run.run_id, actor_id="reviewer-1", session_id="session-1",
            expected_version=run.version,
        )
        return self.store.transition(
            approved.run_id, expected_version=approved.version, to_state="running",
            lease_owner=worker_id, lease_seconds=60,
        )

    def test_create_persists_ordered_step_metadata_without_arguments(self):
        run = self.store.create(
            idempotency_key="step-records",
            workflow_name="release-check",
            definition=[
                {"name": "review", "action": "emit", "argument": "do-not-copy", "requires_approval": True},
                {"name": "notify", "action": "note", "argument": "done", "requires_approval": False},
            ],
        )
        steps = self.store.list_steps(run.run_id)
        self.assertEqual([step.step_index for step in steps], [0, 1])
        self.assertEqual([step.name for step in steps], ["review", "notify"])
        self.assertEqual([step.action for step in steps], ["emit", "note"])
        self.assertEqual([step.requires_approval for step in steps], [True, False])
        self.assertTrue(all(step.status == "pending" and step.attempts == 0 for step in steps))
        self.assertTrue(all(len(step.input_digest) == 64 for step in steps))
        self.assertNotIn("do-not-copy", repr(steps))
        self.assertEqual(steps[0].step_id, f"{run.run_id}:0")
        self.assertEqual(self.store.list_steps("unknown-run"), [])

    def test_operation_journal_is_idempotent_and_never_stores_request_plaintext(self):
        run = self.start_worker_run("operation-journal")
        request = {"recipient": "private@example.test", "body": "sensitive payload"}
        first = self.store.prepare_operation(
            run.run_id, idempotency_key="send-once", action="message.send", request=request,
        )
        second = self.store.prepare_operation(
            run.run_id, idempotency_key="send-once", action="message.send", request=request,
        )
        self.assertEqual(first.operation_id, second.operation_id)
        self.assertEqual(first.status, "prepared")
        self.assertNotIn("sensitive payload", repr(first))
        with self.assertRaises(WorkflowConflict):
            self.store.prepare_operation(
                run.run_id, idempotency_key="send-once", action="message.send",
                request={"recipient": "other@example.test"},
            )

    def test_operation_dispatch_requires_live_worker_lease_and_records_outcome(self):
        run = self.start_worker_run("operation-dispatch")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="provider-call-1", action="provider.call",
            request={"input_digest": "abc"},
        )
        inflight = self.store.mark_operation_in_flight(
            operation.operation_id, worker_id="worker-a", expected_version=operation.version,
            provider_name="test-provider",
        )
        self.assertEqual(inflight.status, "in_flight")
        resolved = self.store.record_operation_outcome(
            inflight.operation_id, expected_version=inflight.version,
            status="succeeded", provider_reference="provider-op-123",
            outcome={"accepted": True},
        )
        self.assertEqual(resolved.status, "succeeded")
        self.assertEqual(resolved.provider_reference, "provider-op-123")
        self.assertEqual(self.store.list_unresolved_operations(), [])

    def test_task_cancellation_is_blocked_during_external_dispatch(self):
        run = self.start_worker_run("operation-cancel-race")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="cancel-race", action="provider.call",
            request={"input_digest": "cancel"},
        )
        inflight = self.store.mark_operation_in_flight(
            operation.operation_id, worker_id="worker-a", expected_version=operation.version,
            provider_name="test-provider",
        )
        with self.assertRaises(WorkflowTransitionError):
            self.store.transition(
                run.run_id, expected_version=run.version, to_state="cancelled",
            )
        self.assertEqual(self.store.get(run.run_id).state, "running")
        self.assertEqual(self.store.get_operation(inflight.operation_id).status, "in_flight")
        resolved = self.store.record_operation_outcome(
            inflight.operation_id, expected_version=inflight.version,
            status="succeeded", provider_reference="provider-cancel-race",
        )
        cancelled = self.store.transition(
            run.run_id, expected_version=run.version, to_state="cancelled",
        )
        self.assertEqual(resolved.status, "succeeded")
        self.assertEqual(cancelled.state, "cancelled")

    def test_operation_audit_captures_atomic_redacted_lifecycle(self):
        run = self.start_worker_run("operation-audit")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="audit-once", action="provider.call",
            request={"secret": "must-not-appear"},
        )
        inflight = self.store.mark_operation_in_flight(
            operation.operation_id, worker_id="worker-a", expected_version=operation.version,
            provider_name="test-provider",
        )
        completed = self.store.record_operation_outcome(
            inflight.operation_id, expected_version=inflight.version,
            status="succeeded", provider_reference="provider-123",
            outcome={"accepted": True},
        )
        events = self.store.list_operation_events(operation.operation_id)
        self.assertEqual([event.to_status for event in events],
                         ["prepared", "in_flight", "succeeded"])
        self.assertEqual([event.sequence for event in events], [1, 2, 3])
        self.assertEqual(events[1].actor_type, "worker")
        self.assertEqual(events[1].actor_id, "worker-a")
        self.assertNotIn("must-not-appear", repr(events))
        self.assertEqual(completed.status, "succeeded")
        with self.store._connect() as db:
            with self.assertRaises(Exception):
                db.execute(
                    "UPDATE operation_events SET reason='tampered' WHERE event_id=?",
                    (events[0].event_id,),
                )
            with self.assertRaises(Exception):
                db.execute("DELETE FROM operation_events WHERE event_id=?", (events[0].event_id,))

    def test_operation_audit_insert_failure_rolls_back_state_transition(self):
        run = self.start_worker_run("operation-audit-rollback")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="audit-rollback", action="provider.call",
            request={"input_digest": "abc"},
        )
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER fail_operation_event_insert
                BEFORE INSERT ON operation_events
                WHEN NEW.to_status='in_flight'
                BEGIN SELECT RAISE(ABORT, 'injected audit write failure'); END
            """)
        with self.assertRaises(Exception):
            self.store.mark_operation_in_flight(
                operation.operation_id, worker_id="worker-a", expected_version=operation.version,
            provider_name="test-provider",
        )
        self.assertEqual(self.store.get_operation(operation.operation_id).status, "prepared")
        self.assertEqual(
            [event.to_status for event in self.store.list_operation_events(operation.operation_id)],
            ["prepared"],
        )

    def test_abandoned_inflight_operation_requires_expired_lease_and_is_not_replayed(self):
        run = self.start_worker_run("operation-abandoned")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="provider-uncertain", action="provider.call",
            request={"input_digest": "abc"},
        )
        inflight = self.store.mark_operation_in_flight(
            operation.operation_id, worker_id="worker-a", expected_version=operation.version,
            provider_name="test-provider",
        )
        with self.assertRaises(WorkflowTransitionError):
            self.store.reconcile_abandoned_operation(
                inflight.operation_id, expected_version=inflight.version,
            )
        expired_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), run.run_id),
            )
        reconciled = self.store.reconcile_abandoned_operation(
            inflight.operation_id, expected_version=inflight.version, now=expired_at,
        )
        self.assertEqual(reconciled.status, "needs_reconciliation")
        self.assertEqual(reconciled.version, inflight.version + 1)
        self.assertEqual(len(self.store.list_unresolved_operations(task_id=run.run_id)), 1)

    def test_reconciled_operation_requires_provider_matched_digest_evidence(self):
        run = self.start_worker_run("operation-reconcile-evidence")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="reconcile-evidence", action="provider.call",
            request={"input_digest": "evidence"},
        )
        inflight = self.store.mark_operation_in_flight(
            operation.operation_id, worker_id="worker-a",
            expected_version=operation.version, provider_name="test-provider",
        )
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(), run.run_id),
            )
        expired_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        uncertain = self.store.reconcile_abandoned_operation(
            inflight.operation_id, expected_version=inflight.version, now=expired_at,
        )
        digest = "a" * 64
        with self.assertRaises(WorkflowTransitionError):
            self.store.resolve_reconciled_operation(
                uncertain.operation_id, expected_version=uncertain.version,
                provider_reference="provider-123", evidence_source="other-provider",
                evidence_digest=digest,
            )
        with self.assertRaises(ValueError):
            self.store.resolve_reconciled_operation(
                uncertain.operation_id, expected_version=uncertain.version,
                provider_reference="provider-123", evidence_source="test-provider",
                evidence_digest="not-a-digest",
            )
        resolved = self.store.resolve_reconciled_operation(
            uncertain.operation_id, expected_version=uncertain.version,
            provider_reference="provider-123", evidence_source="test-provider",
            evidence_digest=digest, principal_id="reviewer-1",
        )
        self.assertEqual(resolved.status, "succeeded")
        self.assertEqual(resolved.outcome["evidence_source"], "test-provider")
        self.assertEqual(resolved.outcome["evidence_sha256"], digest)
        events = self.store.list_operation_events(operation.operation_id)
        self.assertEqual(events[-1].principal_id, "reviewer-1")
        self.assertEqual(events[-1].to_status, "succeeded")

    def test_operation_dispatch_revalidates_approval_inside_claim_transaction(self):
        run = self.start_worker_run("operation-approval-recheck")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="approval-recheck", action="provider.call",
            request={"input_digest": "approval"},
        )
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET approval_digest='stale' WHERE run_id=?",
                (run.run_id,),
            )
        with self.assertRaises(WorkflowTransitionError):
            self.store.mark_operation_in_flight(
                operation.operation_id, worker_id="worker-a", expected_version=operation.version,
            provider_name="test-provider",
        )
        self.assertEqual(self.store.get_operation(operation.operation_id).status, "prepared")

    def test_operation_dispatch_rejects_wrong_worker_and_stale_version(self):
        run = self.start_worker_run("operation-dispatch-guard")
        operation = self.store.prepare_operation(
            run.run_id, idempotency_key="provider-call-guard", action="provider.call",
            request={"input_digest": "abc"},
        )
        with self.assertRaises(WorkflowTransitionError):
            self.store.mark_operation_in_flight(
                operation.operation_id, worker_id="worker-b", expected_version=operation.version,
            provider_name="test-provider",
        )
        with self.assertRaises(WorkflowConflict):
            self.store.mark_operation_in_flight(
                operation.operation_id, worker_id="worker-a", expected_version=operation.version + 1,
            provider_name="test-provider",
        )

    def test_idempotent_create_does_not_duplicate_step_records(self):
        first = self.create(key="step-idempotency")
        second = self.create(key="step-idempotency")
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(len(self.store.list_steps(first.run_id)), 1)

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
            result={"steps_completed": 1}, expected_lease_owner="worker-a",
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

    def test_running_terminal_transition_requires_worker_identity(self):
        run = self.create()
        run = self.store.approve(
            run.run_id, actor_id="reviewer-1", session_id="session-1", expected_version=run.version
        )
        running = self.store.transition(
            run.run_id, expected_version=run.version, to_state="running", lease_owner="worker-a"
        )
        with self.assertRaises(WorkflowConflict):
            self.store.transition(
                running.run_id, expected_version=running.version, to_state="succeeded",
                result={"steps_completed": 1},
            )

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
