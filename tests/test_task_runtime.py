from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore, WorkflowTransitionError


class TaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        self.grants = {"task:create", "task:read", "task:progress", "task:artifact",
                       "task:lease:renew", "task:complete", "task:fail", "task:cancel",
                       "task:retry", "task:list", "task:step:start", "task:step:finish", "task:step:reconcile"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: (actor == "alice" and action in self.grants) or (actor == "supervisor" and action in {"task:recover", "task:step:resolve"}),
        )
        self.steps = [{"name": "review", "action": "emit", "argument": "inspect",
                       "requires_approval": False}]

    def create(self, key="task-1"):
        return self.runtime.enqueue(actor_id="alice", idempotency_key=key,
                                    name="test-task", steps=self.steps)

    def claim(self, task):
        return self.store.claim_next(worker_id="worker-a")

    def test_enqueue_and_creation_event_roll_back_together(self):
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_creation_event
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.created'
                BEGIN SELECT RAISE(ABORT, 'simulated creation journal failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.enqueue(actor_id="alice", idempotency_key="atomic-create",
                                 name="atomic-create", steps=self.steps)
        with self.store._connect() as db:
            run = db.execute(
                "SELECT run_id FROM workflow_runs WHERE idempotency_key=?",
                ("atomic-create",),
            ).fetchone()
            self.assertIsNone(run)

    def test_enqueue_is_idempotent_and_event_is_persisted(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first.task_id, second.task_id)
        events = self.runtime.events(actor_id="alice", task_id=first.task_id)
        self.assertEqual([e.kind for e in events], ["task.created"])
        self.assertEqual(events[0].payload["step_count"], 1)

    def test_task_listing_requires_separate_capability_and_is_bounded(self):
        first = self.create("list-first")
        second = self.create("list-second")
        listed = self.runtime.list_tasks(actor_id="alice", limit=1)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].task_id, second.task_id)
        with self.assertRaises(PermissionError):
            self.runtime.list_tasks(actor_id="supervisor")
        with self.assertRaises(ValueError):
            self.runtime.list_tasks(actor_id="alice", limit=101)
        with self.assertRaises(ValueError):
            self.runtime.list_tasks(actor_id="alice", state="unknown")

    def test_redacted_dashboard_summaries_exclude_task_payloads(self):
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="dashboard-private",
            name="private-review",
            steps=[{"name": "review", "action": "emit",
                    "argument": "private customer data",
                    "requires_approval": False}],
        )
        summaries = self.runtime.list_task_summaries(actor_id="alice", state="queued", limit=10)
        summary = next(item for item in summaries if item.task_id == task.task_id)
        self.assertEqual(summary.name, "private-review")
        self.assertEqual(summary.state, "queued")
        self.assertFalse(hasattr(summary, "steps"))
        self.assertFalse(hasattr(summary, "result"))
        with self.assertRaises(PermissionError):
            self.runtime.list_task_summaries(actor_id="supervisor")

    def test_event_stream_supports_bounded_incremental_pages(self):
        task = self.create("event-pagination")
        claimed = self.store.claim_next(worker_id="worker-a")
        first = self.runtime.record_progress(
            actor_id="alice", task_id=task.task_id,
            expected_version=claimed.version, message="first update", percent=25,
        )
        self.runtime.record_progress(
            actor_id="alice", task_id=task.task_id,
            expected_version=first.version, message="second update", percent=50,
        )
        page_one = self.runtime.events(
            actor_id="alice", task_id=task.task_id, after_sequence=2, limit=1,
        )
        page_two = self.runtime.events(
            actor_id="alice", task_id=task.task_id,
            after_sequence=page_one[-1].sequence, limit=1,
        )
        self.assertEqual([event.sequence for event in page_one], [3])
        self.assertEqual([event.sequence for event in page_two], [4])
        self.assertEqual(page_one[0].payload["message"], "first update")
        with self.assertRaises(ValueError):
            self.runtime.events(actor_id="alice", task_id=task.task_id, limit=501)
        with self.assertRaises(PermissionError):
            self.runtime.events(actor_id="supervisor", task_id=task.task_id)

    def test_supervisor_recovery_quarantines_running_steps_after_lease_expiry(self):
        task = self.create("expired-step-recovery")
        self.claim(task)
        step = self.runtime.list_steps(actor_id="alice", task_id=task.task_id)[0]
        self.runtime.transition_step(
            actor_id="alice", task_id=task.task_id, step_id=step.step_id,
            worker_id="worker-a", expected_step_version=0, to_status="running",
        )
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), task.task_id),
            )
        with self.assertRaises(PermissionError):
            self.runtime.recover_expired(actor_id="alice", task_id=task.task_id)
        recovered = self.runtime.recover_expired(actor_id="supervisor", task_id=task.task_id)
        self.assertEqual(recovered.state, "interrupted")
        self.assertIsNone(recovered.lease_owner)
        quarantined = self.store.list_steps(task.task_id)[0]
        self.assertEqual((quarantined.status, quarantined.version), ("needs_reconciliation", 2))
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual([event.kind for event in events], [
            "task.created", "task.claimed", "task.step.started",
            "task.step.needs_reconciliation", "task.interrupted",
        ])
        self.assertEqual(events[-1].actor_id, "supervisor")
        with self.assertRaises(WorkflowTransitionError):
            self.runtime.recover_expired(actor_id="supervisor", task_id=task.task_id)

    def test_supervisor_reconciliation_is_audited_and_does_not_resume_task(self):
        task = self.create("reconcile-step")
        self.claim(task)
        step = self.runtime.list_steps(actor_id="alice", task_id=task.task_id)[0]
        self.runtime.transition_step(
            actor_id="alice", task_id=task.task_id, step_id=step.step_id,
            worker_id="worker-a", expected_step_version=0, to_status="running",
        )
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                (task.task_id,),
            )
        self.runtime.recover_expired(actor_id="supervisor", task_id=task.task_id)
        with self.assertRaises(PermissionError):
            self.runtime.reconcile_step(
                actor_id="alice", task_id=task.task_id, step_id=step.step_id,
                expected_step_version=2, decision="succeeded", reason="verified",
            )
        with self.assertRaises(ValueError):
            self.runtime.reconcile_step(
                actor_id="supervisor", task_id=task.task_id, step_id=step.step_id,
                expected_step_version=2, decision="succeeded", reason=" ",
            )
        with self.assertRaises(ValueError):
            self.runtime.reconcile_step(
                actor_id="supervisor", task_id=task.task_id, step_id=step.step_id,
                expected_step_version=2, decision=[], reason="invalid decision type",
            )
        with self.assertRaises(ValueError):
            self.runtime.reconcile_step(
                actor_id="supervisor", task_id=task.task_id, step_id=step.step_id,
                expected_step_version=2, decision="failed", reason="contains\x00nul",
            )
        resolved = self.runtime.reconcile_step(
            actor_id="supervisor", task_id=task.task_id, step_id=step.step_id,
            expected_step_version=2, decision="succeeded",
            reason="External operation verified in provider audit log.",
        )
        self.assertEqual((resolved.status, resolved.version), ("succeeded", 3))
        current = self.store.get(task.task_id)
        self.assertEqual(current.state, "interrupted")
        self.assertIsNone(current.lease_owner)
        event = self.runtime.events(actor_id="alice", task_id=task.task_id)[-1]
        self.assertEqual(event.kind, "task.step.reconciled")
        self.assertEqual(event.actor_id, "supervisor")
        self.assertFalse(event.payload["workflow_resumed"])
        self.assertEqual(event.payload["decision"], "succeeded")
        with self.assertRaises(WorkflowConflict):
            self.runtime.reconcile_step(
                actor_id="supervisor", task_id=task.task_id, step_id=step.step_id,
                expected_step_version=2, decision="failed", reason="stale decision",
            )

    def test_reconciliation_event_failure_rolls_back_step_disposition(self):
        task = self.create("reconcile-atomic")
        self.claim(task)
        step = self.runtime.list_steps(actor_id="alice", task_id=task.task_id)[0]
        self.runtime.transition_step(
            actor_id="alice", task_id=task.task_id, step_id=step.step_id,
            worker_id="worker-a", expected_step_version=0, to_status="running",
        )
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                (task.task_id,),
            )
        self.runtime.recover_expired(actor_id="supervisor", task_id=task.task_id)
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_reconciliation_event
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.step.reconciled'
                BEGIN SELECT RAISE(ABORT, 'simulated reconciliation journal failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.reconcile_step(
                actor_id="supervisor", task_id=task.task_id, step_id=step.step_id,
                expected_step_version=2, decision="failed", reason="provider confirms failure",
            )
        current = self.store.list_steps(task.task_id)[0]
        self.assertEqual((current.status, current.version), ("needs_reconciliation", 2))
        self.assertEqual(self.store.get(task.task_id).state, "interrupted")

    def test_concurrent_recovery_has_single_winner(self):
        task = self.create("concurrent-recovery")
        self.claim(task)
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                (task.task_id,),
            )

        def recover():
            try:
                return self.runtime.recover_expired(
                    actor_id="supervisor", task_id=task.task_id,
                )
            except WorkflowTransitionError:
                return "already-recovered"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: recover(), range(2)))
        self.assertEqual(sum(outcome != "already-recovered" for outcome in outcomes), 1)
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual(
            sum(event.kind == "task.interrupted" for event in events), 1,
        )
        self.assertEqual(self.store.get(task.task_id).state, "interrupted")

    def test_bulk_recovery_pages_oldest_expired_runs_without_starvation(self):
        tasks = []
        for index in range(3):
            task = self.create(f"bulk-expired-{index}")
            self.claim(task)
            tasks.append(task)
        with self.store._connect() as db:
            for index, task in enumerate(tasks):
                db.execute(
                    "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                    (f"2000-01-0{index + 1}T00:00:00+00:00", task.task_id),
                )
        recovered_ids = []
        for _ in range(3):
            page = self.runtime.recover_expired(
                actor_id="supervisor", limit=1,
            )
            self.assertEqual(len(page), 1)
            recovered_ids.append(page[0].task_id)
        self.assertEqual(set(recovered_ids), {task.task_id for task in tasks})
        self.assertEqual(
            self.runtime.recover_expired(actor_id="supervisor", limit=1), [],
        )

    def test_bulk_recovery_skips_malformed_and_timezone_naive_expiries(self):
        for index, expiry in enumerate(("not-a-timestamp", "2000-01-01T00:00:00")):
            task = self.create(f"bulk-invalid-expiry-{index}")
            self.claim(task)
            with self.store._connect() as db:
                db.execute(
                    "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                    (expiry, task.task_id),
                )
        self.assertEqual(
            self.runtime.recover_expired(actor_id="supervisor", limit=10), [],
        )
        self.assertTrue(all(
            self.store.get(f"task-{index}") is None for index in range(2)
        ))

    def test_recovery_rejects_malformed_or_timezone_naive_lease_expiry(self):
        for invalid_expiry in ("not-a-timestamp", "2000-01-01T00:00:00"):
            with self.subTest(invalid_expiry=invalid_expiry):
                task = self.create("invalid-expiry-" + str(len(invalid_expiry)))
                self.claim(task)
                with self.store._connect() as db:
                    db.execute(
                        "UPDATE workflow_runs SET lease_expires_at=? WHERE run_id=?",
                        (invalid_expiry, task.task_id),
                    )
                with self.assertRaises(WorkflowConflict):
                    self.runtime.recover_expired(
                        actor_id="supervisor", task_id=task.task_id,
                    )
                self.assertEqual(self.store.get(task.task_id).state, "running")

    def test_recovery_requires_expired_lease(self):
        task = self.create("live-step-recovery")
        self.claim(task)
        with self.assertRaises(WorkflowConflict):
            self.runtime.recover_expired(actor_id="supervisor", task_id=task.task_id)

    def test_step_transitions_are_lease_bound_versioned_and_journaled(self):
        task = self.create("step-transitions")
        claimed = self.claim(task)
        step = self.runtime.list_steps(actor_id="alice", task_id=task.task_id)[0]
        self.assertEqual(step.status, "pending")
        started = self.runtime.transition_step(
            actor_id="alice", task_id=task.task_id, step_id=step.step_id,
            worker_id="worker-a", expected_step_version=step.version, to_status="running",
        )
        self.assertEqual((started.status, started.attempts, started.version), ("running", 1, 1))
        finished = self.runtime.transition_step(
            actor_id="alice", task_id=task.task_id, step_id=step.step_id,
            worker_id="worker-a", expected_step_version=started.version, to_status="succeeded",
        )
        self.assertEqual((finished.status, finished.version), ("succeeded", 2))
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual([event.kind for event in events],
                         ["task.created", "task.claimed", "task.step.started", "task.step.succeeded"])
        self.assertEqual(events[-1].payload["step_id"], step.step_id)
        self.assertEqual(events[-1].actor_id, "alice")
        with self.assertRaises(WorkflowConflict):
            self.runtime.transition_step(
                actor_id="alice", task_id=task.task_id, step_id=step.step_id,
                worker_id="worker-a", expected_step_version=started.version, to_status="succeeded",
            )

    def test_step_transition_rejects_wrong_worker_and_missing_permission(self):
        task = self.create("step-worker-guard")
        self.claim(task)
        step = self.runtime.list_steps(actor_id="alice", task_id=task.task_id)[0]
        with self.assertRaises(WorkflowConflict):
            self.runtime.transition_step(
                actor_id="alice", task_id=task.task_id, step_id=step.step_id,
                worker_id="worker-b", expected_step_version=0, to_status="running",
            )
        restricted = TaskRuntime(self.store, authorize=lambda actor, action, target: action == "task:read")
        with self.assertRaises(PermissionError):
            restricted.transition_step(
                actor_id="alice", task_id=task.task_id, step_id=step.step_id,
                worker_id="worker-a", expected_step_version=0, to_status="running",
            )
        self.assertEqual(self.store.list_steps(task.task_id)[0].status, "pending")

    def test_step_state_and_event_roll_back_together(self):
        task = self.create("atomic-step-event")
        self.claim(task)
        step = self.runtime.list_steps(actor_id="alice", task_id=task.task_id)[0]
        self.runtime.transition_step(
            actor_id="alice", task_id=task.task_id, step_id=step.step_id,
            worker_id="worker-a", expected_step_version=0, to_status="running",
        )
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_step_success
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.step.succeeded'
                BEGIN SELECT RAISE(ABORT, 'simulated step journal failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.transition_step(
                actor_id="alice", task_id=task.task_id, step_id=step.step_id,
                worker_id="worker-a", expected_step_version=1, to_status="succeeded",
            )
        current = self.store.list_steps(task.task_id)[0]
        self.assertEqual((current.status, current.version), ("running", 1))
        self.assertEqual([event.kind for event in self.runtime.events(
            actor_id="alice", task_id=task.task_id)],
            ["task.created", "task.claimed", "task.step.started"])

    def test_authorization_fails_closed(self):
        with self.assertRaises(PermissionError):
            TaskRuntime(self.store).enqueue(actor_id="alice", idempotency_key="no-auth",
                                            name="task", steps=self.steps)
        broken = TaskRuntime(self.store, authorize=lambda *_: (_ for _ in ()).throw(RuntimeError()))
        with self.assertRaises(PermissionError):
            broken.enqueue(actor_id="alice", idempotency_key="broken-auth",
                           name="task", steps=self.steps)

    def test_claim_is_journaled_atomically_with_worker_identity(self):
        task = self.create("claim-event")
        claimed = self.claim(task)
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual([event.kind for event in events], ["task.created", "task.claimed"])
        self.assertEqual(events[-1].actor_id, "worker-a")
        self.assertEqual(events[-1].payload["attempt"], 1)
        self.assertEqual(events[-1].payload["lease_expires_at"], claimed.lease_expires_at)

    def test_claim_and_claim_event_roll_back_together(self):
        task = self.create("atomic-claim-event")
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_claim_event
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.claimed'
                BEGIN SELECT RAISE(ABORT, 'simulated claim journal failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.claim_next(worker_id="worker-a")
        current = self.store.get(task.task_id)
        self.assertEqual(current.state, "queued")
        self.assertEqual(current.version, task.version)
        self.assertIsNone(current.lease_owner)
        self.assertEqual([event.kind for event in self.runtime.events(
            actor_id="alice", task_id=task.task_id)], ["task.created"])

    def test_lease_renewal_is_journaled_with_worker_identity(self):
        task = self.create("lease-renewal-event")
        claimed = self.claim(task)
        renewed = self.runtime.renew_lease(
            actor_id="alice", task_id=task.task_id, worker_id="worker-a",
            expected_version=claimed.version, lease_seconds=120,
        )
        event = self.runtime.events(actor_id="alice", task_id=task.task_id)[-1]
        self.assertEqual(event.kind, "task.lease_renewed")
        self.assertEqual(event.actor_id, "alice")
        self.assertEqual(event.payload["worker_id"], "worker-a")
        self.assertEqual(event.payload["lease_expires_at"], renewed.lease_expires_at)

    def test_lease_renewal_and_event_roll_back_together(self):
        task = self.create("atomic-lease-renewal")
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_lease_renewal_event
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.lease_renewed'
                BEGIN SELECT RAISE(ABORT, 'simulated lease event failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.renew_lease(
                actor_id="alice", task_id=task.task_id, worker_id="worker-a",
                expected_version=claimed.version, lease_seconds=120,
            )
        current = self.store.get(task.task_id)
        self.assertEqual(current.version, claimed.version)
        self.assertEqual(current.lease_expires_at, claimed.lease_expires_at)
        self.assertEqual([event.kind for event in self.runtime.events(
            actor_id="alice", task_id=task.task_id)],
            ["task.created", "task.claimed"])

    def test_progress_is_versioned_and_durable(self):
        task = self.create()
        claimed = self.claim(task)
        updated = self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
            expected_version=claimed.version, message="Analyzing", percent=25)
        self.assertEqual(updated.version, claimed.version + 1)
        with self.assertRaises(WorkflowConflict):
            self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, message="stale")
        self.assertEqual([e.kind for e in self.runtime.events(actor_id="alice", task_id=task.task_id)],
                         ["task.created", "task.claimed", "task.progress"])

    def test_artifact_is_journaled(self):
        task = self.create()
        claimed = self.claim(task)
        self.runtime.record_artifact(actor_id="alice", task_id=task.task_id,
            expected_version=claimed.version, artifact_type="report",
            reference="artifact://report-1", metadata={"format": "markdown"})
        event = self.runtime.events(actor_id="alice", task_id=task.task_id)[-1]
        self.assertEqual(event.kind, "task.artifact")
        self.assertEqual(event.payload["reference"], "artifact://report-1")

    def test_only_current_worker_can_complete(self):
        task = self.create()
        claimed = self.claim(task)
        with self.assertRaises(WorkflowTransitionError):
            self.runtime.complete(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, worker_id="worker-b", result={})
        done = self.runtime.complete(actor_id="alice", task_id=task.task_id,
            expected_version=claimed.version, worker_id="worker-a", result={"ok": True})
        self.assertEqual(done.state, "succeeded")
        self.assertEqual(self.runtime.events(actor_id="alice", task_id=task.task_id)[-1].kind,
                         "task.completed")

    def test_cancellation_and_terminal_event_roll_back_together(self):
        task = self.create("atomic-cancellation-event")
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_cancel_event
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.cancelled'
                BEGIN SELECT RAISE(ABORT, 'simulated event journal failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.cancel(actor_id="alice", task_id=task.task_id,
                                expected_version=task.version)
        current = self.store.get(task.task_id)
        self.assertEqual(current.state, "queued")
        self.assertEqual(current.version, task.version)
        self.assertEqual([event.kind for event in self.runtime.events(
            actor_id="alice", task_id=task.task_id)], ["task.created"])

    def test_completion_and_terminal_event_roll_back_together(self):
        task = self.create("atomic-completion-event")
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_terminal_event
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.completed'
                BEGIN SELECT RAISE(ABORT, 'simulated event journal failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.complete(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, worker_id="worker-a", result={"ok": True})
        current = self.store.get(task.task_id)
        self.assertEqual(current.state, "running")
        self.assertEqual(current.version, claimed.version)
        self.assertEqual([event.kind for event in self.runtime.events(
            actor_id="alice", task_id=task.task_id)], ["task.created", "task.claimed"])

    def test_expired_lease_cannot_complete(self):
        task = self.create()
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                       (task.task_id,))
        with self.assertRaises(WorkflowConflict):
            self.runtime.complete(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, worker_id="worker-a", result={})

    def test_expired_lease_cannot_fail_task(self):
        task = self.create()
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                       (task.task_id,))
        with self.assertRaises(WorkflowConflict):
            self.runtime.fail(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, worker_id="worker-a",
                error_code="provider_error")

    def test_cancel_is_versioned_and_terminal(self):
        task = self.create()
        cancelled = self.runtime.cancel(actor_id="alice", task_id=task.task_id,
                                         expected_version=task.version)
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(self.runtime.events(actor_id="alice", task_id=task.task_id)[-1].kind,
                         "task.cancelled")
        with self.assertRaises(WorkflowTransitionError):
            self.runtime.cancel(actor_id="alice", task_id=task.task_id,
                                 expected_version=cancelled.version)


    def test_retry_requires_permission_and_failed_or_interrupted_state(self):
        task = self.create("retry-permission")
        with self.assertRaises(PermissionError):
            self.runtime.retry_failed(actor_id="supervisor", task_id=task.task_id,
                                      expected_version=task.version)
        with self.assertRaises(WorkflowTransitionError):
            self.runtime.retry_failed(actor_id="alice", task_id=task.task_id,
                                      expected_version=task.version)

    def test_retry_creates_fresh_task_with_provenance_and_is_idempotent(self):
        task = self.create("retry-source")
        claimed = self.claim(task)
        failed = self.runtime.fail(actor_id="alice", task_id=task.task_id,
                                   expected_version=claimed.version, worker_id="worker-a",
                                   error_code="provider_error")
        retried = self.runtime.retry_failed(actor_id="alice", task_id=task.task_id,
                                             expected_version=failed.version)
        self.assertNotEqual(retried.task_id, task.task_id)
        self.assertEqual(retried.state, "queued")
        self.assertEqual(retried.attempts, 0)
        events = self.runtime.events(actor_id="alice", task_id=retried.task_id)
        self.assertEqual([event.kind for event in events], ["task.created"])
        self.assertEqual(events[0].payload["retry_of"], task.task_id)
        again = self.runtime.retry_failed(actor_id="alice", task_id=task.task_id,
                                          expected_version=failed.version)
        self.assertEqual(again.task_id, retried.task_id)

    def test_retry_rejects_stale_version_and_requires_fresh_approval(self):
        protected_steps = [{"name": "protected", "action": "emit", "argument": "work",
                            "requires_approval": True}]
        task = self.runtime.enqueue(actor_id="alice", idempotency_key="retry-protected",
                                    name="protected-task", steps=protected_steps)
        approved = self.store.approve(task.task_id, actor_id="alice", session_id="test-session",
                                      expected_version=task.version)
        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertIsNotNone(claimed)
        failed = self.runtime.fail(actor_id="alice", task_id=task.task_id,
                                   expected_version=claimed.version, worker_id="worker-a",
                                   error_code="provider_error")
        retried = self.runtime.retry_failed(actor_id="alice", task_id=task.task_id,
                                            expected_version=failed.version)
        with self.assertRaises(WorkflowConflict):
            self.runtime.retry_failed(actor_id="alice", task_id=task.task_id,
                                      expected_version=task.version)
        self.assertIsNone(self.store.claim_next(worker_id="worker-b"))
        self.assertEqual(self.store.get(retried.task_id).state, "queued")

    def test_invalid_progress_payload_is_rejected(self):
        task = self.create()
        claimed = self.claim(task)
        with self.assertRaises(ValueError):
            self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, message="x" * 1001)
        with self.assertRaises(ValueError):
            self.runtime.record_progress(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, message="ok", percent=True)

    def test_expired_lease_recovery_and_event_roll_back_together(self):
        task = self.create("recover-atomic")
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                (task.task_id,),
            )
            db.execute("""
                CREATE TRIGGER reject_interruption_event
                BEFORE INSERT ON task_events
                WHEN NEW.kind = 'task.interrupted'
                BEGIN SELECT RAISE(ABORT, 'simulated interruption journal failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.recover_expired(actor_id="supervisor")
        current = self.store.get(task.task_id)
        self.assertEqual(current.state, "running")
        self.assertEqual(current.version, claimed.version)
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual([event.kind for event in events], ["task.created", "task.claimed"])

    def test_expired_lease_recovery_is_authorized_and_journaled(self):
        task = self.create("recover-expired")
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                       (task.task_id,))
        with self.assertRaises(PermissionError):
            self.runtime.recover_expired(actor_id="alice")
        recovered = self.runtime.recover_expired(actor_id="supervisor")
        self.assertEqual([item.task_id for item in recovered], [task.task_id])
        self.assertEqual(recovered[0].state, "interrupted")
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual(events[-1].kind, "task.interrupted")
        self.assertFalse(events[-1].payload["automatic_replay"])


if __name__ == "__main__":
    unittest.main()
