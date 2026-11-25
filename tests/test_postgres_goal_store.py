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

    def _run(self, suffix):
        return self.runs.create(
            idempotency_key=self.suffix + "-" + suffix,
            workflow_name="goal-child",
            definition=[{"name": "work", "action": "emit", "argument": "safe",
                         "requires_approval": False}],
            tenant_id=self.tenant,
            owner_principal_id=self.owner,
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
        dependent = self._run("dependency-dependent")
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
        blocked = self.runs.get(dependent.run_id)
        self.assertFalse(blocked.dispatch_ready)
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
        released = self.runs.get(dependent.run_id)
        self.assertTrue(released.dispatch_ready)
        started_dependent = self.runs.transition_with_event(
            dependent.run_id, expected_version=released.version,
            to_state="running", event_kind="task.started",
            actor_id=self.owner, event_payload={},
        )
        self.assertEqual(started_dependent.state, "running")

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
