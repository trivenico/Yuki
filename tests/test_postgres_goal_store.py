"""PostgreSQL integration coverage for tenant-scoped workflow goals.

Uses the disposable YUKI_TEST_POSTGRES_URL database shared with the workflow
repository integration suite. Append-only goal records are intentionally not
deleted by tests; each test uses unique identifiers.
"""
import os
from pathlib import Path
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresWorkflowGoalStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        from automation.postgres_goal_store import PostgresWorkflowGoalStore
        cls.run_store_type = PostgresWorkflowRunStore
        cls.goal_store_type = PostgresWorkflowGoalStore
        root = Path(__file__).resolve().parents[1]
        with psycopg.connect(POSTGRES_URL) as conn:
            for name in ("0007_workflow_runs.sql", "0009_external_operations.sql"):
                conn.execute((root / "migrations/security" / name).read_text(encoding="utf-8"), prepare=False)
            has_ownership = conn.execute(
                "SELECT 1 FROM pg_constraint WHERE conname='workflow_runs_ownership_status_check'"
            ).fetchone()
            if not has_ownership:
                conn.execute(
                    (root / "migrations/security/0010_workflow_task_ownership.sql").read_text(encoding="utf-8"),
                    prepare=False,
                )
            conn.execute(
                (root / "migrations/security/0020_workflow_dispatch_visibility_gate.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            has_goals = conn.execute("SELECT to_regclass('public.workflow_goals')").fetchone()[0]
            if has_goals is None:
                conn.execute(
                    (root / "migrations/security/0021_distributed_workflow_goals.sql").read_text(encoding="utf-8"),
                    prepare=False,
                )
            conn.execute(
                (root / "migrations/security/0022_workflow_dependency_readiness.sql").read_text(encoding="utf-8"),
                prepare=False,
            )

    def setUp(self):
        self.suffix = uuid.uuid4().hex
        self.tenant = "goal-tenant-" + self.suffix
        self.owner = "human:goal-" + self.suffix
        self.goal_id = "goal-" + self.suffix
        self.runs = self.run_store_type(POSTGRES_URL, max_pool_size=2)
        self.goals = self.goal_store_type(POSTGRES_URL, max_pool_size=2)

    def tearDown(self):
        self.goals.close()
        self.runs.close()

    def _run(self, suffix, *, dispatch_ready=True):
        return self.runs.create(
            idempotency_key=self.suffix + "-" + suffix,
            workflow_name="goal-child",
            definition=[{"name": "work", "action": "emit", "argument": "safe",
                         "requires_approval": False}],
            tenant_id=self.tenant,
            owner_principal_id=self.owner,
            dispatch_ready=dispatch_ready,
        )

    def _goal(self):
        return self.goals.create_goal(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id,
        )

    def test_goal_membership_is_owner_scoped_and_idempotent(self):
        self._goal()
        run = self._run("membership")
        self.assertTrue(self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=run.run_id,
        ))
        self.assertFalse(self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=run.run_id,
        ))
        with self.assertRaises(KeyError):
            self.goals.get_goal(
                tenant_id=self.tenant, owner_principal_id="human:other",
                goal_id=self.goal_id,
            )

    def test_dependency_graph_rejects_cycles(self):
        self._goal()
        first, second = self._run("first"), self._run("second")
        for run in (first, second):
            self.goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=run.run_id,
            )
        self.assertTrue(self.goals.add_dependency(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=first.run_id,
            depends_on_task_id=second.run_id,
        ))
        from automation.postgres_goal_store import PostgresGoalConflict
        with self.assertRaisesRegex(PostgresGoalConflict, "dependency_cycle"):
            self.goals.add_dependency(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=second.run_id,
                depends_on_task_id=first.run_id,
            )

    def test_task_membership_rejects_cross_tenant_workflow_run(self):
        self._goal()
        other = self.runs.create(
            idempotency_key=self.suffix + "-other-tenant",
            workflow_name="goal-child",
            definition=[{"name": "work", "action": "emit", "argument": "safe",
                         "requires_approval": False}],
            tenant_id="other-tenant-" + self.suffix,
            owner_principal_id="human:other-" + self.suffix,
        )
        with self.assertRaises(psycopg.IntegrityError):
            self.goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=other.run_id,
            )

    def test_concurrent_opposite_dependency_edges_cannot_create_cycle(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from automation.postgres_goal_store import PostgresGoalConflict

        self._goal()
        first, second = self._run("concurrent-first"), self._run("concurrent-second")
        for run in (first, second):
            self.goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=run.run_id,
            )
        barrier = Barrier(2)

        def add_edge(task_id, dependency_id):
            barrier.wait(timeout=5)
            try:
                return self.goals.add_dependency(
                    tenant_id=self.tenant, owner_principal_id=self.owner,
                    goal_id=self.goal_id, task_id=task_id,
                    depends_on_task_id=dependency_id,
                )
            except PostgresGoalConflict:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda pair: add_edge(*pair),
                [(first.run_id, second.run_id), (second.run_id, first.run_id)],
            ))
        self.assertEqual(sum(results), 1)

    def test_dependent_dispatch_waits_for_all_prerequisites_to_succeed(self):
        from automation.workflow_store import WorkflowTransitionError

        self._goal()
        prerequisite = self._run("dependency-prerequisite")
        prerequisite_two = self._run("dependency-prerequisite-two")
        dependent = self._run("dependency-dependent")
        self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=prerequisite.run_id,
        )
        for run in (prerequisite_two, dependent):
            self.goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=run.run_id,
            )
        self.goals.add_dependency(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=dependent.run_id,
            depends_on_task_id=prerequisite.run_id,
        )
        self.goals.add_dependency(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=dependent.run_id,
            depends_on_task_id=prerequisite_two.run_id,
        )
        blocked = self.runs.get(dependent.run_id)
        with psycopg.connect(POSTGRES_URL) as conn:
            dependency_ready = conn.execute(
                "SELECT dependency_ready FROM workflow_runs WHERE run_id=%s",
                (dependent.run_id,),
            ).fetchone()[0]
        self.assertFalse(dependency_ready)
        with self.assertRaises(WorkflowTransitionError):
            self.runs.transition_with_event(
                dependent.run_id, expected_version=blocked.version,
                to_state="running", event_kind="task.started",
                actor_id=self.owner, event_payload={},
            )

        started = self.runs.transition_with_event(
            prerequisite.run_id, expected_version=prerequisite.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        completed = self.runs.transition_with_event(
            prerequisite.run_id, expected_version=started.version,
            to_state="succeeded", event_kind="task.completed",
            actor_id=self.owner, event_payload={},
        )
        self.assertEqual(completed.state, "succeeded")
        with psycopg.connect(POSTGRES_URL) as conn:
            dependency_ready = conn.execute(
                "SELECT dependency_ready FROM workflow_runs WHERE run_id=%s",
                (dependent.run_id,),
            ).fetchone()[0]
        self.assertFalse(dependency_ready)
        still_blocked = self.runs.get(dependent.run_id)
        with self.assertRaises(WorkflowTransitionError):
            self.runs.transition_with_event(
                dependent.run_id, expected_version=still_blocked.version,
                to_state="running", event_kind="task.started",
                actor_id=self.owner, event_payload={},
            )
        second_started = self.runs.transition_with_event(
            prerequisite_two.run_id, expected_version=prerequisite_two.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        self.runs.transition_with_event(
            prerequisite_two.run_id, expected_version=second_started.version,
            to_state="succeeded", event_kind="task.completed",
            actor_id=self.owner, event_payload={},
        )
        released = self.runs.get(dependent.run_id)
        with psycopg.connect(POSTGRES_URL) as conn:
            dependency_ready = conn.execute(
                "SELECT dependency_ready FROM workflow_runs WHERE run_id=%s",
                (dependent.run_id,),
            ).fetchone()[0]
        self.assertTrue(dependency_ready)
        started_dependent = self.runs.transition_with_event(
            dependent.run_id, expected_version=released.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        self.assertEqual(started_dependent.state, "running")

    def test_adding_edge_to_already_failed_prerequisite_fails_dependent(self):
        self._goal()
        prerequisite = self._run("already-failed-prerequisite")
        dependent = self._run("already-failed-dependent")
        for run in (prerequisite, dependent):
            self.goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=run.run_id,
            )
        started = self.runs.transition_with_event(
            prerequisite.run_id, expected_version=prerequisite.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        self.runs.transition_with_event(
            prerequisite.run_id, expected_version=started.version,
            to_state="failed", event_kind="task.failed",
            actor_id=self.owner, event_payload={"error_code": "worker_failed"},
            error_code="worker_failed",
        )
        self.assertEqual(self.runs.get(dependent.run_id).state, "queued")
        self.goals.add_dependency(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=dependent.run_id,
            depends_on_task_id=prerequisite.run_id,
        )
        failed_dependent = self.runs.get(dependent.run_id)
        self.assertEqual(failed_dependent.state, "failed")
        self.assertEqual(failed_dependent.error_code, "dependency_failed")
        goal = self.goals.get_goal(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id,
        )
        self.assertEqual(goal["state"], "completed")
        self.assertEqual(goal["outcome"], "failed")

    def test_failed_prerequisite_fails_queued_descendants_atomically(self):
        self._goal()
        prerequisite = self._run("failed-prerequisite")
        dependent = self._run("failed-dependent")
        downstream = self._run("failed-downstream")
        for run in (prerequisite, dependent, downstream):
            self.goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=run.run_id,
            )
        self.goals.add_dependency(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=dependent.run_id,
            depends_on_task_id=prerequisite.run_id,
        )
        self.goals.add_dependency(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=downstream.run_id,
            depends_on_task_id=dependent.run_id,
        )
        started = self.runs.transition_with_event(
            prerequisite.run_id, expected_version=prerequisite.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        failed = self.runs.transition_with_event(
            prerequisite.run_id, expected_version=started.version,
            to_state="failed", event_kind="task.failed",
            actor_id=self.owner, event_payload={"error_code": "worker_failed"},
            error_code="worker_failed",
        )
        self.assertEqual(failed.state, "failed")
        failed_dependent = self.runs.get(dependent.run_id)
        failed_downstream = self.runs.get(downstream.run_id)
        self.assertEqual(failed_dependent.state, "failed")
        self.assertEqual(failed_dependent.error_code, "dependency_failed")
        self.assertEqual(failed_downstream.state, "failed")
        self.assertEqual(failed_downstream.error_code, "dependency_failed")
        with psycopg.connect(POSTGRES_URL) as conn:
            event_rows = conn.execute(
                """SELECT task_id, kind FROM task_events
                   WHERE task_id = ANY(%s) AND kind='task.failed'""",
                ([dependent.run_id, downstream.run_id],),
            ).fetchall()
        self.assertEqual({row[0] for row in event_rows}, {dependent.run_id, downstream.run_id})
        goal = self.goals.get_goal(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id,
        )
        self.assertEqual(goal["state"], "completed")
        self.assertEqual(goal["outcome"], "failed")

    def test_goal_cancellation_cancels_queued_tasks_and_preserves_running_tasks(self):
        self._goal()
        queued = self._run("goal-cancel-queued")
        running = self._run("goal-cancel-running")
        for run in (queued, running):
            self.goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id, task_id=run.run_id,
            )
        started = self.runs.transition_with_event(
            running.run_id, expected_version=running.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        cancelled_goal = self.goals.cancel_goal(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, actor_id=self.owner,
        )
        self.assertEqual(cancelled_goal["state"], "cancelled")
        self.assertEqual(self.runs.get(queued.run_id).state, "cancelled")
        self.assertEqual(self.runs.get(running.run_id).state, "running")
        events = self.goals.list_events(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id,
        )
        self.assertEqual(sum(e["event_type"] == "goal.cancelled" for e in events), 1)
        self.assertEqual(sum(e["event_type"] == "task.attached" for e in events), 2)
        repeated = self.goals.cancel_goal(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, actor_id=self.owner,
        )
        self.assertEqual(repeated["state"], "cancelled")
        self.assertEqual(
            sum(e["event_type"] == "goal.cancelled" for e in self.goals.list_events(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id,
            )), 1,
        )

    def test_goal_cancellation_preserves_held_dispatch_task(self):
        self._goal()
        held = self._run("goal-cancel-held", dispatch_ready=False)
        self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=held.run_id,
        )
        result = self.goals.cancel_goal(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, actor_id=self.owner,
        )
        persisted = self.runs.get(held.run_id)
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(persisted.state, "queued")
        self.assertFalse(persisted.dispatch_ready)

    def test_dependency_release_does_not_bypass_dispatch_visibility_gate(self):
        from automation.workflow_store import WorkflowTransitionError

        self._goal()
        prerequisite = self._run("visibility-prerequisite")
        dependent = self._run("visibility-dependent", dispatch_ready=False)
        self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=prerequisite.run_id,
        )
        self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=dependent.run_id,
        )
        self.goals.add_dependency(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=dependent.run_id,
            depends_on_task_id=prerequisite.run_id,
        )
        started = self.runs.transition_with_event(
            prerequisite.run_id, expected_version=prerequisite.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        self.runs.transition_with_event(
            prerequisite.run_id, expected_version=started.version,
            to_state="succeeded", event_kind="task.completed",
            actor_id=self.owner, event_payload={},
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            gates = conn.execute(
                "SELECT dispatch_ready, dependency_ready FROM workflow_runs WHERE run_id=%s",
                (dependent.run_id,),
            ).fetchone()
        self.assertEqual(gates, (False, True))
        released = self.runs.get(dependent.run_id)
        with self.assertRaisesRegex(WorkflowTransitionError, "dispatch is not visible"):
            self.runs.transition_with_event(
                dependent.run_id, expected_version=released.version,
                to_state="running", event_kind="task.started",
                actor_id=self.owner, event_payload={},
            )

    def test_interrupted_child_produces_failed_goal_outcome(self):
        self._goal()
        run = self._run("interrupted")
        self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=run.run_id,
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET state='interrupted' WHERE run_id=%s",
                (run.run_id,),
            )
        goal = self.goals.refresh_outcome(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id,
        )
        self.assertEqual(goal["state"], "completed")
        self.assertEqual(goal["outcome"], "failed")

    def test_terminal_workflow_transition_updates_goal_in_same_transaction(self):
        self._goal()
        run = self._run("cancelled-atomically")
        self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=run.run_id,
        )
        changed = self.runs.transition_with_event(
            run.run_id, expected_version=run.version, to_state="cancelled",
            event_kind="task.cancelled", actor_id=self.owner,
            event_payload={"reason": "test"},
        )
        self.assertEqual(changed.state, "cancelled")
        goal = self.goals.get_goal(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id,
        )
        self.assertEqual(goal["state"], "completed")
        self.assertEqual(goal["outcome"], "cancelled")
        events = self.goals.list_events(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id,
        )
        self.assertEqual([event["event_type"] for event in events].count("goal.completed"), 1)

    def test_goal_event_failure_rolls_back_terminal_workflow_transition(self):
        from psycopg import sql

        self._goal()
        run = self._run("event-failure-rollback")
        self.goals.attach_task(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            goal_id=self.goal_id, task_id=run.run_id,
        )
        started = self.runs.transition_with_event(
            run.run_id, expected_version=run.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                sql.SQL(
                    """CREATE OR REPLACE FUNCTION yuki_test_fail_goal_completion()
                       RETURNS trigger LANGUAGE plpgsql AS $
                       BEGIN
                           IF NEW.event_type = 'goal.completed' AND NEW.goal_id = {}
                           THEN RAISE EXCEPTION 'injected_goal_event_failure'; END IF;
                           RETURN NEW;
                       END; $"""
                ).format(sql.Literal(self.goal_id)),
                prepare=False,
            )
            conn.execute(
                """CREATE TRIGGER yuki_test_fail_goal_completion_trigger
                   BEFORE INSERT ON workflow_goal_events
                   FOR EACH ROW EXECUTE FUNCTION yuki_test_fail_goal_completion()"""
            )
        try:
            with self.assertRaises(psycopg.Error):
                self.runs.transition_with_event(
                    run.run_id, expected_version=started.version,
                    to_state="succeeded", event_kind="task.completed",
                    actor_id=self.owner, event_payload={},
                )
            persisted = self.runs.get(run.run_id)
            self.assertEqual(persisted.state, "running")
            goal = self.goals.get_goal(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=self.goal_id,
            )
            self.assertEqual(goal["state"], "active")
            self.assertIsNone(goal["outcome"])
            with psycopg.connect(POSTGRES_URL) as conn:
                terminal_events = conn.execute(
                    "SELECT count(*) FROM task_events WHERE task_id=%s AND kind='task.completed'",
                    (run.run_id,),
                ).fetchone()[0]
            self.assertEqual(terminal_events, 0)
        finally:
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute("DROP TRIGGER IF EXISTS yuki_test_fail_goal_completion_trigger ON workflow_goal_events")
                conn.execute("DROP FUNCTION IF EXISTS yuki_test_fail_goal_completion()")

    def test_goal_event_history_rejects_mutation(self):
        self._goal()
        with self.assertRaises(psycopg.IntegrityError):
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute(
                    """UPDATE workflow_goal_events SET event_type='rewritten'
                       WHERE tenant_id=%s AND goal_id=%s""",
                    (self.tenant, self.goal_id),
                )


if __name__ == "__main__":
    unittest.main()
