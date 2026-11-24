"""PostgreSQL delegation chain and transaction-bound verification tests."""
import os
import unittest
import uuid
import time

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None
_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresDelegationAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.postgres_delegation_authority import PostgresDelegationAuthority
        cls.authority_type = PostgresDelegationAuthority
        from pathlib import Path
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0008_workload_delegations.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        self.authority = self.authority_type(POSTGRES_URL, max_pool_size=3)
        self.now = int(time.time())
        self.task = "delegation-test-" + uuid.uuid4().hex
        self.generation = "17"

    def tearDown(self):
        self.authority.close()

    def grant(self, **overrides):
        args = dict(
            grantor_id="human:owner", grantee_id="workload:agent",
            tenant_id="tenant-a", agent_id="agent-a", task_id=self.task,
            lease_generation=self.generation, scopes={"task:progress", "task:complete"},
            expires_at=self.now + 300, now=self.now,
        )
        args.update(overrides)
        return self.authority.create_grant(**args)

    def test_exact_context_scope_and_parent_revocation(self):
        parent = self.grant()
        child = self.grant(
            grantor_id="workload:agent", grantee_id="workload:child",
            scopes={"task:progress"}, expires_at=self.now + 200,
            parent_id=parent.delegation_id,
        )
        valid = self.authority.verify(
            child.delegation_id, grantee_id="workload:child",
            tenant_id="tenant-a", agent_id="agent-a", task_id=self.task,
            lease_generation=self.generation, scopes={"task:progress"},
            now=self.now + 1,
        )
        self.assertEqual(valid.delegation_id, child.delegation_id)
        with self.assertRaises(PermissionError):
            self.authority.verify(
                child.delegation_id, grantee_id="workload:child",
                tenant_id="tenant-a", agent_id="agent-a", task_id=self.task,
                lease_generation="18", scopes={"task:progress"}, now=self.now + 1,
            )
        self.assertTrue(self.authority.revoke(parent.delegation_id, revoked_by="human:owner", now=self.now + 2))
        with self.assertRaises(PermissionError):
            self.authority.verify(
                child.delegation_id, grantee_id="workload:child",
                tenant_id="tenant-a", agent_id="agent-a", task_id=self.task,
                lease_generation=self.generation, scopes={"task:progress"},
                now=self.now + 3,
            )

    def test_child_cannot_escalate_scope_or_expiry(self):
        parent = self.grant(scopes={"task:progress"}, expires_at=self.now + 120)
        with self.assertRaises(PermissionError):
            self.grant(
                grantor_id="workload:agent", grantee_id="workload:child",
                scopes={"task:complete"}, expires_at=self.now + 100,
                parent_id=parent.delegation_id,
            )
        with self.assertRaises(PermissionError):
            self.grant(
                grantor_id="workload:agent", grantee_id="workload:child",
                scopes={"task:progress"}, expires_at=self.now + 121,
                parent_id=parent.delegation_id,
            )

    def test_task_runtime_accepts_matching_postgres_repository_and_authority(self):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        from automation.task_runtime import TaskRuntime
        store = PostgresWorkflowRunStore(POSTGRES_URL, max_pool_size=2)
        try:
            runtime = TaskRuntime(
                store, authorize=lambda actor, action, task: True,
                delegation_authority=self.authority,
            )
            self.assertIs(runtime.delegation_authority, self.authority)
        finally:
            store.close()

    def test_transaction_verification_rolls_back_with_failed_task_mutation(self):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        from automation.workflow_store import WorkflowConflict
        store = PostgresWorkflowRunStore(POSTGRES_URL, max_pool_size=2)
        key = "delegated-task-" + uuid.uuid4().hex
        try:
            run = store.create(
                idempotency_key=key, workflow_name="delegated",
                definition=[{"name": "work", "action": "emit", "argument": "safe",
                             "requires_approval": False}],
            )
            claimed = store.claim_next(worker_id="workload:agent")
            grant = self.grant(task_id=claimed.run_id, lease_generation=claimed.lease_generation,
                               scopes={"task:progress"})
            fence = lambda db: self.authority.verify_in_transaction(
                db, grant.delegation_id, grantee_id="workload:agent",
                tenant_id="tenant-a", agent_id="agent-a", task_id=claimed.run_id,
                lease_generation=claimed.lease_generation, scopes={"task:progress"},
                now=self.now,
            )
            with self.assertRaises(WorkflowConflict):
                store.append_task_event(
                    claimed.run_id, kind="task.progress", actor_id="worker",
                    payload={"message": "stale"}, expected_version=claimed.version + 10,
                    expected_lease_owner="workload:agent",
                    expected_lease_generation=claimed.lease_generation,
                    delegation_fence=fence,
                )
            with psycopg.connect(POSTGRES_URL) as conn:
                rolled_back = conn.execute(
                    "SELECT count(*) FROM workload_delegation_events WHERE delegation_id=%s AND event_type='verified'",
                    (grant.delegation_id,),
                ).fetchone()[0]
            self.assertEqual(rolled_back, 0)
            store.append_task_event(
                claimed.run_id, kind="task.progress", actor_id="worker",
                payload={"message": "accepted"}, expected_version=claimed.version,
                expected_lease_owner="workload:agent",
                expected_lease_generation=claimed.lease_generation,
                delegation_fence=fence,
            )
            with psycopg.connect(POSTGRES_URL) as conn:
                committed = conn.execute(
                    "SELECT count(*) FROM workload_delegation_events WHERE delegation_id=%s AND event_type='verified'",
                    (grant.delegation_id,),
                ).fetchone()[0]
            self.assertEqual(committed, 1)
        finally:
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute("DELETE FROM workflow_runs WHERE idempotency_key=%s", (key,))
            store.close()

    def test_verification_audit_and_revocation_are_durable(self):
        grant = self.grant()
        self.authority.verify(
            grant.delegation_id, grantee_id="workload:agent", tenant_id="tenant-a",
            agent_id="agent-a", task_id=self.task, lease_generation=self.generation,
            scopes={"task:progress"}, now=self.now + 1,
        )
        self.authority.revoke(grant.delegation_id, revoked_by="human:owner", now=self.now + 2)
        with psycopg.connect(POSTGRES_URL) as conn:
            events = conn.execute(
                "SELECT event_type,actor_id FROM workload_delegation_events "
                "WHERE delegation_id=%s ORDER BY event_id", (grant.delegation_id,),
            ).fetchall()
        self.assertEqual(events, [("issued", "human:owner"),
                                  ("verified", "workload:agent"),
                                  ("revoked", "human:owner")])


if __name__ == "__main__":
    unittest.main()
