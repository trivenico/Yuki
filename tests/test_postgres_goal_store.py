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
            for name in (
                "0007_workflow_runs.sql", "0009_external_operations.sql",
                "0010_workflow_task_ownership.sql",
                "0020_workflow_dispatch_visibility_gate.sql",
                "0021_distributed_workflow_goals.sql",
            ):
                conn.execute((root / "migrations/security" / name).read_text(encoding="utf-8"), prepare=False)

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
