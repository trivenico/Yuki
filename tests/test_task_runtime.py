import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from automation.workflow_store import WorkflowConflict, WorkflowRunStore, WorkflowTransitionError


class TaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        self.grants = {"task:create", "task:read", "task:progress", "task:artifact",
                       "task:complete", "task:fail", "task:cancel", "task:retry", "task:list", "task:approve",\n                       "task:spec:register", "task:spec:read", "task:spec:list", "task:dependency"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: (actor == "alice" and action in self.grants) or (actor == "supervisor" and action == "task:recover"),
        )
        self.steps = [{"name": "review", "action": "emit", "argument": "inspect",
                       "requires_approval": False}]

    def create(self, key="task-1"):
        return self.runtime.enqueue(actor_id="alice", idempotency_key=key,
                                    name="test-task", steps=self.steps)

    def claim(self, task):
        return self.store.claim_next(worker_id="worker-a")

    def test_task_dependency_waits_for_prerequisite_success(self):
        prerequisite = self.create("dependency-prerequisite")
        dependent = self.runtime.enqueue(
            actor_id="alice", idempotency_key="dependency-dependent",
            name="test-task", steps=self.steps, dependencies=(prerequisite.task_id,),
        )
        self.assertEqual(dependent.dependencies, (prerequisite.task_id,))
        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertEqual(claimed.run_id, prerequisite.task_id)
        completed = self.runtime.complete(
            actor_id="alice", task_id=claimed.run_id, expected_version=claimed.version,
            worker_id="worker-b", result={"ok": True},
        )
        self.assertEqual(completed.state, "succeeded")
        next_claim = self.store.claim_next(worker_id="worker-b")
        self.assertEqual(next_claim.run_id, dependent.task_id)

    def test_store_rejects_retry_lineage_from_nonfailed_or_modified_source(self):
        queued = self.create("invalid-retry-lineage-source")
        with self.assertRaises(WorkflowTransitionError):
            self.store.create(
                idempotency_key="invalid-retry-lineage",
                workflow_name=queued.name,
                definition=queued.steps,
                retry_of_task_id=queued.task_id,
            )
        failed = self.runtime.fail(
            actor_id="alice", task_id=queued.task_id,
            expected_version=self.store.claim_next(worker_id="worker-a").version,
            worker_id="worker-a", error_code="transient_failure",
        )
        with self.assertRaises(WorkflowConflict):
            self.store.create(
                idempotency_key="modified-retry-lineage",
                workflow_name=failed.name,
                definition=[{"name": "different", "action": "emit", "argument": "x",
                             "requires_approval": False}],
                retry_of_task_id=failed.task_id,
            )

    def test_retry_preserves_its_own_prerequisite_edges(self):
        root = self.runtime.enqueue(
            actor_id="alice", idempotency_key="retry-edge-root",
            name="test-task", steps=self.steps,
        )
        child = self.runtime.enqueue(
            actor_id="alice", idempotency_key="retry-edge-child",
            name="test-task", steps=self.steps, dependencies=(root.task_id,),
        )
        root_claim = self.store.claim_next(worker_id="worker-root")
        self.assertEqual(root_claim.run_id, root.task_id)
        self.runtime.complete(
            actor_id="alice", task_id=root.task_id,
            expected_version=root_claim.version, worker_id="worker-root",
            result={"ok": True},
        )
        child_claim = self.store.claim_next(worker_id="worker-child")
        self.assertEqual(child_claim.run_id, child.task_id)
        failed = self.runtime.fail(
            actor_id="alice", task_id=child.task_id,
            expected_version=child_claim.version, worker_id="worker-child",
            error_code="transient_failure",
        )
        retry = self.runtime.retry_failed(
            actor_id="alice", task_id=failed.task_id, expected_version=failed.version,
        )
        self.assertEqual(
            self.store.get_dependencies(retry.task_id), (root.task_id,),
        )

    def test_successful_retry_satisfies_original_task_dependency(self):
        prerequisite = self.create("retry-lineage-prerequisite")
        dependent = self.runtime.enqueue(
            actor_id="alice", idempotency_key="retry-lineage-dependent",
            name="test-task", steps=self.steps, dependencies=(prerequisite.task_id,),
        )
        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertEqual(claimed.run_id, prerequisite.task_id)
        failed = self.runtime.fail(
            actor_id="alice", task_id=claimed.run_id, expected_version=claimed.version,
            worker_id="worker-a", error_code="transient_failure",
        )
        self.assertIsNone(self.store.claim_next(worker_id="worker-b"))
        retry = self.runtime.retry_failed(
            actor_id="alice", task_id=failed.run_id, expected_version=failed.version,
        )
        retry_events = self.runtime.events(actor_id="alice", task_id=retry.task_id)
        self.assertEqual(retry_events[0].payload["retry_of"], prerequisite.task_id)
        retry_claim = self.store.claim_next(worker_id="worker-c")
        self.assertEqual(retry_claim.run_id, retry.task_id)
        completed = self.runtime.complete(
            actor_id="alice", task_id=retry_claim.run_id,
            expected_version=retry_claim.version, worker_id="worker-c",
            result={"ok": True},
        )
        self.assertEqual(completed.state, "succeeded")
        dependent_claim = self.store.claim_next(worker_id="worker-d")
        self.assertEqual(dependent_claim.run_id, dependent.task_id)

    def test_cancelled_prerequisite_blocks_dependent_with_audit_event(self):
        prerequisite = self.create("cancelled-prerequisite")
        dependent = self.runtime.enqueue(
            actor_id="alice", idempotency_key="cancelled-dependent",
            name="test-task", steps=self.steps, dependencies=(prerequisite.task_id,),
        )
        self.runtime.cancel(
            actor_id="alice", task_id=prerequisite.task_id,
            expected_version=prerequisite.version,
        )
        self.assertIsNone(self.store.claim_next(worker_id="worker-a"))
        blocked = self.runtime.get(actor_id="alice", task_id=dependent.task_id)
        self.assertEqual(blocked.state, "blocked")
        self.assertEqual(blocked.error_code, "dependency_terminal")
        events = self.runtime.events(actor_id="alice", task_id=dependent.task_id)
        self.assertEqual(events[-1].kind, "task.blocked")

    def test_dependency_creation_is_authorized_and_atomic(self):
        prerequisite = self.create("unauthorized-dependency-prerequisite")
        restricted = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "alice" and action == "task:create",
        )
        with self.assertRaises(PermissionError):
            restricted.enqueue(
                actor_id="alice", idempotency_key="unauthorized-dependent",
                name="test-task", steps=self.steps,
                dependencies=(prerequisite.task_id,),
            )
        with self.assertRaises(KeyError):
            self.runtime.enqueue(
                actor_id="alice", idempotency_key="missing-dependent",
                name="test-task", steps=self.steps, dependencies=("missing-task",),
            )
        with self.store._connect() as db:
            row = db.execute(
                "SELECT 1 FROM workflow_runs WHERE idempotency_key=?",
                ("missing-dependent",),
            ).fetchone()
        self.assertIsNone(row)

    def test_agent_specs_are_authorized_immutable_and_persisted(self):
        spec = AgentExecutionSpec(
            spec_id="planner-v1",
            role="planner",
            instructions="Produce a bounded task plan.",
            capabilities=("task:read",),
            budget=ExecutionBudget(max_steps=5),
        )
        registered = self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        self.assertEqual(registered["spec_id"], "planner-v1")
        self.assertEqual(self.runtime.get_agent_spec(actor_id="alice", spec_id="planner-v1"), spec)
        self.assertEqual(self.runtime.list_agent_specs(actor_id="alice"), [spec])

        reopened_store = WorkflowRunStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        reopened_runtime = TaskRuntime(
            reopened_store,
            authorize=lambda actor, action, task: actor == "alice" and action == "task:spec:read",
        )
        self.assertEqual(reopened_runtime.get_agent_spec(actor_id="alice", spec_id="planner-v1"), spec)

    def test_agent_spec_registration_rejects_mutation_and_unauthorized_access(self):
        spec = AgentExecutionSpec(
            spec_id="immutable-agent",
            role="reviewer",
            instructions="Review supplied content.",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        changed = AgentExecutionSpec(
            spec_id="immutable-agent",
            role="reviewer",
            instructions="Review supplied content carefully.",
        )
        with self.assertRaises(WorkflowConflict):
            self.runtime.register_agent_spec(actor_id="alice", spec=changed)
        with self.assertRaises(PermissionError):
            self.runtime.get_agent_spec(actor_id="supervisor", spec_id="immutable-agent")
        with self.assertRaises(PermissionError):
            self.runtime.register_agent_spec(actor_id="supervisor", spec=spec)
        with self.assertRaises(PermissionError):
            self.runtime.list_agent_specs(actor_id="supervisor")

    def test_agent_spec_dependency_graph_registers_atomically_in_order(self):
        worker = AgentExecutionSpec(
            spec_id="graph-worker", role="worker",
            instructions="Perform a bounded delegated task.",
        )
        planner = AgentExecutionSpec(
            spec_id="graph-planner", role="planner",
            instructions="Delegate to the registered worker.",
            dependencies=("graph-worker",),
        )
        registered = self.runtime.register_agent_specs(
            actor_id="alice", specs=(planner, worker),
        )
        self.assertEqual(
            tuple(item["spec_id"] for item in registered),
            ("graph-worker", "graph-planner"),
        )
        self.assertEqual(
            tuple(spec.spec_id for spec in self.runtime.list_agent_specs(actor_id="alice")),
            ("graph-planner", "graph-worker"),
        )

    def test_agent_spec_dependency_graph_rejects_mutation_without_partial_writes(self):
        existing = AgentExecutionSpec(
            spec_id="graph-existing", role="worker",
            instructions="Original worker configuration.",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=existing)
        changed = AgentExecutionSpec(
            spec_id="graph-existing", role="worker",
            instructions="Changed worker configuration.",
        )
        dependent = AgentExecutionSpec(
            spec_id="graph-dependent", role="planner",
            instructions="Use the worker.",
            dependencies=("graph-existing",),
        )
        with self.assertRaises(WorkflowConflict):
            self.runtime.register_agent_specs(
                actor_id="alice", specs=(dependent, changed),
            )
        with self.assertRaises(KeyError):
            self.runtime.get_agent_spec(actor_id="alice", spec_id="graph-dependent")

    def test_agent_spec_dependency_graph_registration_requires_authorization(self):
        worker = AgentExecutionSpec(
            spec_id="unauthorized-graph-worker", role="worker",
            instructions="Perform bounded work.",
        )
        with self.assertRaises(PermissionError):
            self.runtime.register_agent_specs(actor_id="supervisor", specs=(worker,))

    def test_agent_spec_registration_is_idempotent_for_identical_spec(self):
        spec = AgentExecutionSpec(
            spec_id="same-agent",
            role="worker",
            instructions="Perform assigned bounded task.",
        )
        first = self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        second = self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        self.assertEqual(first, second)

    def test_task_binds_immutable_agent_spec_and_enforces_spec_approval(self):
        spec = AgentExecutionSpec(
            spec_id="approved-reviewer",
            role="reviewer",
            instructions="Review the supplied material.",
            approval_policy="required",
            output_types=("text",),
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="bound-review-task",
            name="reviewer", steps=[{
                "name": "review", "action": "emit", "argument": "review this",
                "requires_approval": False,
            }],
            agent_execution_spec_id=spec.spec_id,
        )
        self.assertEqual(task.agent_execution_spec_id, spec.spec_id)
        self.assertIsNone(self.store.claim_next(worker_id="worker-a"))
        approved = self.runtime.approve(
            actor_id="alice", task_id=task.task_id, session_id="approval-session",
            expected_version=task.version,
        )
        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.run_id, task.task_id)
        self.assertEqual(claimed.agent_execution_spec_digest,
                         self.store.get_agent_execution_spec_record(spec.spec_id)[1])

    def test_task_creation_rejects_tampered_agent_spec_before_binding(self):
        spec = AgentExecutionSpec(
            spec_id="tampered-before-enqueue",
            role="reviewer",
            instructions="Review supplied content.",
            approval_policy="required",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        with self.store._connect() as db:
            row = db.execute(
                "SELECT spec_json FROM agent_execution_specs WHERE spec_id=?",
                (spec.spec_id,),
            ).fetchone()
            tampered = json.loads(row["spec_json"])
            tampered["approval_policy"] = "not_requested"
            db.execute(
                "UPDATE agent_execution_specs SET spec_json=? WHERE spec_id=?",
                (json.dumps(tampered, sort_keys=True, separators=(",", ":")), spec.spec_id),
            )
        with self.assertRaisesRegex(WorkflowConflict, "integrity"):
            self.runtime.enqueue(
                actor_id="alice", idempotency_key="tampered-spec-task",
                name="reviewer",
                steps=[{"name": "review", "action": "emit", "argument": "review",
                        "requires_approval": False}],
                agent_execution_spec_id=spec.spec_id,
            )

    def test_corrupted_spec_cannot_downgrade_required_approval(self):
        spec = AgentExecutionSpec(
            spec_id="approval-integrity",
            role="reviewer",
            instructions="Review carefully.",
            approval_policy="required",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="approval-integrity-task",
            name="reviewer", steps=[{
                "name": "review", "action": "emit", "argument": "review",
                "requires_approval": False,
            }], agent_execution_spec_id=spec.spec_id,
        )
        with self.store._connect() as db:
            row = db.execute(
                "SELECT spec_json FROM agent_execution_specs WHERE spec_id=?",
                (spec.spec_id,),
            ).fetchone()
            tampered = json.loads(row["spec_json"])
            tampered["approval_policy"] = "not_requested"
            db.execute(
                "UPDATE agent_execution_specs SET spec_json=? WHERE spec_id=?",
                (json.dumps(tampered, sort_keys=True, separators=(",", ":")), spec.spec_id),
            )
        self.assertIsNone(self.store.claim_next(worker_id="worker-a"))
        self.assertEqual(self.store.get(task.task_id).state, "queued")

    def test_task_spec_binding_is_part_of_idempotency_and_role_contract(self):
        first_spec = AgentExecutionSpec(
            spec_id="writer-v1", role="writer", instructions="Write concise text.",
        )
        second_spec = AgentExecutionSpec(
            spec_id="writer-v2", role="writer", instructions="Write detailed text.",
        )
        self.runtime.register_agent_spec(actor_id="alice", spec=first_spec)
        self.runtime.register_agent_spec(actor_id="alice", spec=second_spec)
        task = self.runtime.enqueue(
            actor_id="alice", idempotency_key="spec-idempotency",
            name="writer", steps=self.steps, agent_execution_spec_id="writer-v1",
        )
        with self.assertRaises(WorkflowConflict):
            self.runtime.enqueue(
                actor_id="alice", idempotency_key="spec-idempotency",
                name="writer", steps=self.steps, agent_execution_spec_id="writer-v2",
            )
        with self.assertRaises(WorkflowTransitionError):
            self.runtime.enqueue(
                actor_id="alice", idempotency_key="wrong-role",
                name="reviewer", steps=self.steps, agent_execution_spec_id="writer-v1",
            )
        with self.assertRaises(KeyError):
            self.runtime.enqueue(
                actor_id="alice", idempotency_key="missing-spec",
                name="writer", steps=self.steps, agent_execution_spec_id="missing-spec",
            )

    def test_enqueue_is_idempotent_and_event_is_persisted(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first.task_id, second.task_id)
        events = self.runtime.events(actor_id="alice", task_id=first.task_id)
        self.assertEqual([e.kind for e in events], ["task.created"])
        self.assertEqual(events[0].payload["step_count"], 1)

    def test_task_creation_and_initial_audit_event_roll_back_together(self):
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_task_created_audit BEFORE INSERT ON task_events
                WHEN NEW.kind='task.created'
                BEGIN SELECT RAISE(ABORT, 'injected task creation audit failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.create("atomic-task-created")
        self.assertEqual(self.store.list_runs(), [])

    def test_idempotent_enqueue_repairs_a_missing_creation_event(self):
        task = self.create("repair-task-created")
        with self.store._connect() as db:
            db.execute("DELETE FROM task_events WHERE task_id=?", (task.task_id,))
        retried = self.create("repair-task-created")
        self.assertEqual(retried.task_id, task.task_id)
        events = self.runtime.events(actor_id="alice", task_id=task.task_id)
        self.assertEqual([event.kind for event in events], ["task.created"])

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
            actor_id="alice", task_id=task.task_id, after_sequence=1, limit=1,
        )
        page_two = self.runtime.events(
            actor_id="alice", task_id=task.task_id,
            after_sequence=page_one[-1].sequence, limit=1,
        )
        self.assertEqual([event.sequence for event in page_one], [2])
        self.assertEqual([event.sequence for event in page_two], [3])
        self.assertEqual(page_one[0].payload["message"], "first update")
        with self.assertRaises(ValueError):
            self.runtime.events(actor_id="alice", task_id=task.task_id, limit=501)
        with self.assertRaises(PermissionError):
            self.runtime.events(actor_id="supervisor", task_id=task.task_id)

    def test_session_revocation_pages_beyond_first_hundred_queued_tasks(self):
        session = "revoked-session"
        protected = "different-session"
        definition = [{
            "name": "review", "action": "emit", "argument": "bounded",
            "requires_approval": True,
        }]
        approved_ids = []
        for index in range(105):
            task = self.runtime.enqueue(
                actor_id="alice", idempotency_key=f"session-page-{index}",
                name="approved-task", steps=definition,
            )
            approved = self.runtime.approve(
                actor_id="alice", task_id=task.task_id, session_id=session,
                expected_version=task.version,
            )
            approved_ids.append(approved.task_id)
        other = self.runtime.enqueue(
            actor_id="alice", idempotency_key="other-session-task",
            name="other-session-task", steps=definition,
        )
        self.runtime.approve(
            actor_id="alice", task_id=other.task_id, session_id=protected,
            expected_version=other.version,
        )
        unapproved = self.runtime.enqueue(
            actor_id="alice", idempotency_key="unapproved-task",
            name="unapproved-task", steps=definition,
        )

        cancelled = self.runtime.revoke_session_approvals(session)
        self.assertEqual({task.task_id for task in cancelled}, set(approved_ids))
        self.assertTrue(all(task.state == "cancelled" for task in cancelled))
        self.assertEqual(self.store.get(other.task_id).state, "queued")
        self.assertEqual(self.store.get(unapproved.task_id).state, "queued")

    def test_approval_and_audit_event_roll_back_together(self):
        definition = [{"name": "protected", "action": "emit", "argument": "work",
                       "requires_approval": True}]
        task = self.runtime.enqueue(actor_id="alice", idempotency_key="atomic-approval",
                                    name="protected-task", steps=definition)
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_approval_audit BEFORE INSERT ON task_events
                WHEN NEW.kind='task.approved'
                BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.approve(actor_id="alice", task_id=task.task_id,
                                 session_id="test-session", expected_version=task.version)
        unchanged = self.store.get(task.task_id)
        self.assertEqual(unchanged.state, "queued")
        self.assertEqual(unchanged.version, task.version)
        self.assertIsNone(unchanged.approved_by)
        self.assertEqual([event.kind for event in self.store.list_task_events(task.task_id)],
                         ["task.created"])

    def test_terminal_transition_and_audit_event_roll_back_together(self):
        task = self.create("atomic-completion")
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_completion_audit BEFORE INSERT ON task_events
                WHEN NEW.kind='task.completed'
                BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.complete(actor_id="alice", task_id=task.task_id,
                                  expected_version=claimed.version, worker_id="worker-a",
                                  result={"ok": True})
        unchanged = self.store.get(task.task_id)
        self.assertEqual(unchanged.state, "running")
        self.assertEqual(unchanged.version, claimed.version)
        self.assertIsNone(unchanged.result)
        self.assertEqual([event.kind for event in self.store.list_task_events(task.task_id)],
                         ["task.created"])

    def test_authorization_fails_closed(self):
        with self.assertRaises(PermissionError):
            TaskRuntime(self.store).enqueue(actor_id="alice", idempotency_key="no-auth",
                                            name="task", steps=self.steps)
        broken = TaskRuntime(self.store, authorize=lambda *_: (_ for _ in ()).throw(RuntimeError()))
        with self.assertRaises(PermissionError):
            broken.enqueue(actor_id="alice", idempotency_key="broken-auth",
                           name="task", steps=self.steps)

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
                         ["task.created", "task.progress"])

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

    def test_expired_lease_cannot_complete(self):
        task = self.create()
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute("UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                       (task.task_id,))
        with self.assertRaises(WorkflowConflict):
            self.runtime.complete(actor_id="alice", task_id=task.task_id,
                expected_version=claimed.version, worker_id="worker-a", result={})

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

    def test_expired_lease_and_interruption_event_roll_back_together(self):
        task = self.create("recover-expired-atomic")
        claimed = self.claim(task)
        with self.store._connect() as db:
            db.execute(
                "UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                (task.task_id,),
            )
            db.execute("""
                CREATE TRIGGER reject_interrupted_audit BEFORE INSERT ON task_events
                WHEN NEW.kind='task.interrupted'
                BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.recover_expired(actor_id="supervisor")
        unchanged = self.store.get(task.task_id)
        self.assertEqual(unchanged.state, "running")
        self.assertEqual(unchanged.version, claimed.version)
        self.assertEqual(unchanged.lease_owner, "worker-a")
        self.assertEqual([event.kind for event in self.store.list_task_events(task.task_id)],
                         ["task.created"])


if __name__ == "__main__":
    unittest.main()
