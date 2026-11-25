"""Real PostgreSQL integration tests for the shared workflow repository.

Set YUKI_TEST_POSTGRES_URL to a disposable PostgreSQL database. These tests
skip when PostgreSQL is unavailable; SQLite is covered by the existing suite.
"""
import os
from pathlib import Path
import threading
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


def _claim_from_separate_process(database_url, worker_id, barrier, results):
    from automation.postgres_workflow_store import PostgresWorkflowRunStore
    store = PostgresWorkflowRunStore(database_url, max_pool_size=1)
    try:
        barrier.wait(timeout=15)
        claimed = store.claim_next(worker_id=worker_id)
        results.put(None if claimed is None else claimed.run_id)
    finally:
        store.close()


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresWorkflowRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        cls.store_type = PostgresWorkflowRunStore
        migration = Path(__file__).resolve().parents[1] / "migrations/security/0007_workflow_runs.sql"
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            root = Path(__file__).resolve().parents[1]
            conn.execute((root / "migrations/security/0009_external_operations.sql").read_text(encoding="utf-8"), prepare=False)
            applied = conn.execute("SELECT 1 FROM pg_constraint WHERE conname=%s", ("workflow_runs_ownership_status_check",)).fetchone()
            if not applied:
                conn.execute((root / "migrations/security/0010_workflow_task_ownership.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute(
                (root / "migrations/security/0020_workflow_dispatch_visibility_gate.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            conn.execute(
                (root / "migrations/security/0022_workflow_dependency_readiness.sql").read_text(encoding="utf-8"),
                prepare=False,
            )

    def setUp(self):
        self.key = "pg-workflow-" + uuid.uuid4().hex
        self.store_a = self.store_type(POSTGRES_URL, max_pool_size=2)
        self.store_b = self.store_type(POSTGRES_URL, max_pool_size=2)
        self.run = self.store_a.create(
            idempotency_key=self.key,
            workflow_name="postgres-contract",
            definition=[{"name": "work", "action": "emit", "argument": "safe",
                         "requires_approval": False}],
        )

    def tearDown(self):
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("DELETE FROM workflow_runs WHERE idempotency_key LIKE %s", (self.key + "%",))
        self.store_a.close()
        self.store_b.close()

    def test_failed_task_is_terminal_and_cannot_accept_more_events(self):
        from automation.task_runtime import TaskRuntime
        from automation.workflow_store import WorkflowTransitionError

        self.assertEqual(self.store_a.claim_next(worker_id="fixture-worker").run_id, self.run.run_id)
        owned = self.store_a.create(
            idempotency_key=self.key + "-failed-terminal",
            workflow_name="failed-terminal", definition=self.run.definition,
        )
        claimed = self.store_a.claim_next(worker_id="failure-worker")
        self.assertEqual(claimed.run_id, owned.run_id)
        runtime = TaskRuntime(self.store_a, authorize=lambda *_args: True)
        failed = runtime.fail(
            actor_id="alice", task_id=owned.run_id,
            expected_version=claimed.version, worker_id="failure-worker",
            error_code="worker_failed",
        )
        with self.assertRaises(WorkflowTransitionError):
            self.store_b.append_task_event(
                owned.run_id, kind="task.progress", actor_id="alice",
                payload={"message": "late progress"}, expected_version=failed.version,
            )
        with self.assertRaises(WorkflowTransitionError):
            self.store_b.transition_with_event(
                owned.run_id, expected_version=failed.version,
                to_state="queued", event_kind="task.created", actor_id="alice",
                event_payload={"retry": True},
            )
        self.assertEqual(self.store_b.get(owned.run_id).state, "failed")

    def test_postgres_repository_satisfies_managed_backend_contract(self):
        from automation.workflow_repository import ManagedWorkflowRepository, WorkflowRepository

        self.assertIsInstance(self.store_a, WorkflowRepository)
        self.assertIsInstance(self.store_a, ManagedWorkflowRepository)
        self.assertTrue(self.store_a.check_ready())
        self.assertEqual(self.store_a.database_identity, self.store_b.database_identity)

    def test_tenant_owner_metadata_is_persisted_and_bound_to_idempotency(self):
        owned_key = self.key + "-owned"
        owned = self.store_a.create(
            idempotency_key=owned_key,
            workflow_name="owned-task",
            definition=self.run.definition,
            tenant_id="tenant-a",
            owner_principal_id="human:alice",
        )
        self.assertEqual(owned.tenant_id, "tenant-a")
        self.assertEqual(owned.owner_principal_id, "human:alice")
        self.assertEqual(owned.ownership_status, "owned")
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store_b.create(
                idempotency_key=owned_key, workflow_name="owned-task",
                definition=self.run.definition, tenant_id="tenant-b",
                owner_principal_id="human:bob",
            )

    def test_held_dispatch_task_is_not_claimable_until_released(self):
        # Consume the ordinary fixture task so the assertion isolates the held row.
        self.assertIsNotNone(self.store_a.claim_next(worker_id="fixture-worker"))
        held_key = self.key + "-held"
        held = self.store_a.create(
            idempotency_key=held_key,
            workflow_name="held-specialist-task",
            definition=self.run.definition,
            tenant_id="tenant-held",
            owner_principal_id="human:held",
            dispatch_ready=False,
        )
        self.assertFalse(held.dispatch_ready)
        self.assertIsNone(self.store_b.claim_next(worker_id="ordinary-worker"))
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store_b.create(
                idempotency_key=held_key,
                workflow_name="held-specialist-task",
                definition=self.run.definition,
                tenant_id="tenant-held",
                owner_principal_id="human:held",
                dispatch_ready=True,
            )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET dispatch_ready=TRUE WHERE run_id=%s",
                (held.run_id,),
            )
        released = self.store_b.get(held.run_id)
        self.assertTrue(released.dispatch_ready)
        claimed = self.store_b.claim_next(worker_id="ordinary-worker")
        self.assertEqual(held.run_id, claimed.run_id)

    def test_task_tenant_ownership_is_immutable(self):
        owned = self.store_a.create(
            idempotency_key=self.key + "-immutable",
            workflow_name="immutable-task", definition=self.run.definition,
            tenant_id="tenant-a", owner_principal_id="human:alice",
        )
        with self.assertRaises(psycopg.IntegrityError):
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute(
                    "UPDATE workflow_runs SET tenant_id=%s WHERE run_id=%s",
                    ("tenant-b", owned.run_id),
                )

    def test_verified_workload_context_is_tenant_and_lease_bound(self):
        import time
        from core.workload_identity import WorkloadCredentialClaims
        from automation.task_runtime import TaskRuntime

        now = int(time.time())
        self.store_a.claim_next(worker_id="fixture-worker")
        owned = self.store_a.create(
            idempotency_key=self.key + "-verified",
            workflow_name="verified-action",
            definition=self.run.definition,
            tenant_id="tenant-a",
            owner_principal_id="human:alice",
        )
        claimed = self.store_a.claim_next(worker_id="worker-agent")
        self.assertEqual(claimed.run_id, owned.run_id)
        claims = WorkloadCredentialClaims(
            issuer="https://issuer.yuki.internal",
            subject="worker-agent", tenant_id="tenant-a", agent_id="agent-1",
            audience="yuki-external-action", issued_at=now, not_before=now,
            expires_at=now + 120, token_id="token-" + uuid.uuid4().hex,
            task_id=owned.run_id, lease_generation=claimed.lease_generation,
            scopes=frozenset({"calendar.create"}),
        )

        class Verifier:
            def verify(self, credential, **kwargs):
                self.assertion = credential
                return claims

        from core.authorization import AuthorizationDecision, Principal
        from core.authorization_gateway import AuthorizationGateway

        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "decision-1", "test-policy", "allowed")

        gateway = AuthorizationGateway(Policy(), audit=lambda request, decision: True)
        runtime = TaskRuntime(
            self.store_a, authorization_gateway=gateway,
            principal_for_actor=lambda actor: Principal(actor, "human", tenant_id="tenant-a"),
            workload_credential_verifier=Verifier(),
            workload_audience="yuki-external-action", credential_clock=lambda: now,
            policy_revision="test-policy",
        )
        context = runtime.verify_workload_context(
            task_id=owned.run_id, worker_id="worker-agent",
            credential="opaque-test-credential", action="calendar.create",
        )
        self.assertEqual(context.tenant_id, "tenant-a")
        self.assertEqual(context.owner_principal_id, "human:alice")
        self.assertEqual(context.agent_id, "agent-1")
        self.assertEqual(context.task_id, owned.run_id)
        self.assertEqual(context.lease_generation, claimed.lease_generation)
        self.assertIn("calendar.create", context.scopes)
        self.assertEqual(context.authorized_action, "calendar.create")
        self.assertEqual(context.authorization_decision_id, "decision-1")
        self.assertEqual(context.policy_revision, "test-policy")
        with self.assertRaises(AttributeError):
            context.tenant_id = "tenant-b"
        with self.assertRaises(PermissionError):
            runtime.verify_workload_context(
                task_id=owned.run_id, worker_id="worker-agent",
                credential="opaque-test-credential", action="calendar.delete",
            )

    def test_verified_workload_context_rejects_tenant_mismatch(self):
        import time
        from core.workload_identity import WorkloadCredentialClaims
        from automation.task_runtime import TaskRuntime

        now = int(time.time())
        self.store_a.claim_next(worker_id="fixture-worker")
        owned = self.store_a.create(
            idempotency_key=self.key + "-wrong-tenant",
            workflow_name="wrong-tenant",
            definition=self.run.definition,
            tenant_id="tenant-a",
            owner_principal_id="human:alice",
        )
        claimed = self.store_a.claim_next(worker_id="worker-wrong-tenant")
        claims = WorkloadCredentialClaims(
            issuer="https://issuer.yuki.internal", subject="worker-wrong-tenant",
            tenant_id="tenant-b", agent_id="agent-1", audience="yuki-external-action",
            issued_at=now, not_before=now, expires_at=now + 120,
            token_id="token-" + uuid.uuid4().hex, task_id=owned.run_id,
            lease_generation=claimed.lease_generation,
            scopes=frozenset({"calendar.create"}),
        )

        class Verifier:
            def verify(self, credential, **kwargs):
                return claims

        from core.authorization import AuthorizationDecision, Principal
        from core.authorization_gateway import AuthorizationGateway

        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "decision-tenant", "test-policy", "allowed")

        gateway = AuthorizationGateway(Policy(), audit=lambda request, decision: True)
        runtime = TaskRuntime(
            self.store_a, authorization_gateway=gateway,
            principal_for_actor=lambda actor: Principal(actor, "human", tenant_id="tenant-a"),
            workload_credential_verifier=Verifier(),
            workload_audience="yuki-external-action", credential_clock=lambda: now,
            policy_revision="test-policy",
        )
        with self.assertRaises(PermissionError):
            runtime.verify_workload_context(
                task_id=owned.run_id, worker_id="worker-wrong-tenant",
                credential="opaque-test-credential", action="calendar.create",
            )

    def test_running_task_cannot_be_cancelled_while_worker_may_have_side_effects(self):
        from automation.task_runtime import TaskRuntime
        from automation.workflow_store import WorkflowTransitionError

        # Consume the baseline fixture so the following claim is unambiguous.
        self.assertIsNotNone(self.store_a.claim_next(worker_id="fixture-worker"))
        owned = self.store_a.create(
            idempotency_key=self.key + "-running-cancel",
            workflow_name="running-cancel-safety",
            definition=self.run.definition,
            tenant_id="tenant-a",
            owner_principal_id="human:alice",
        )
        claimed = self.store_a.claim_next(worker_id="active-worker")
        self.assertEqual(claimed.run_id, owned.run_id)
        runtime = TaskRuntime(self.store_a, authorize=lambda *_args: True)
        with self.assertRaisesRegex(
            WorkflowTransitionError, "running task cannot be cancelled safely"
        ):
            runtime.cancel(
                actor_id="human:alice", task_id=owned.run_id,
                expected_version=claimed.version,
            )
        unchanged = self.store_b.get(owned.run_id)
        self.assertEqual(unchanged.state, "running")
        self.assertEqual(unchanged.lease_owner, "active-worker")

    def test_postgres_task_runtime_fails_closed_without_authenticated_tenant_context(self):
        from automation.task_runtime import TaskRuntime
        runtime = TaskRuntime(self.store_a, authorize=lambda *_: True)
        with self.assertRaises(PermissionError):
            runtime.enqueue(
                actor_id="alice", idempotency_key="missing-tenant-" + uuid.uuid4().hex,
                name="unowned", steps=self.run.definition,
            )

    def test_idempotency_and_payload_conflict(self):
        same = self.store_b.create(
            idempotency_key=self.key, workflow_name="postgres-contract",
            definition=self.run.definition,
        )
        self.assertEqual(same.run_id, self.run.run_id)
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store_b.create(
                idempotency_key=self.key, workflow_name="different",
                definition=self.run.definition,
            )

    def test_independent_connections_only_claim_once(self):
        barrier = threading.Barrier(2)
        claims, errors = [], []

        def claim(store, worker):
            try:
                barrier.wait(timeout=5)
                claims.append(store.claim_next(worker_id=worker))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=claim, args=(self.store_a, "worker_a")),
            threading.Thread(target=claim, args=(self.store_b, "worker_b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        winners = [item for item in claims if item is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].run_id, self.run.run_id)
        self.assertTrue(winners[0].lease_generation)

    def test_independent_processes_claim_distinct_runs(self):
        second = self.store_a.create(
            idempotency_key=self.key + "-second", workflow_name="postgres-contract",
            definition=self.run.definition,
        )
        context = __import__("multiprocessing").get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        workers = [
            context.Process(target=_claim_from_separate_process,
                            args=(POSTGRES_URL, "process_a", barrier, results)),
            context.Process(target=_claim_from_separate_process,
                            args=(POSTGRES_URL, "process_b", barrier, results)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=25)
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            self.assertEqual(worker.exitcode, 0)
        claimed_ids = [results.get(timeout=5), results.get(timeout=5)]
        self.assertEqual(set(claimed_ids), {self.run.run_id, second.run_id})

    def test_approval_and_event_commit_together_before_claim(self):
        self.store_a.claim_next(worker_id="worker_a")
        protected = self.store_a.create(
            idempotency_key=self.key + "-approval", workflow_name="protected",
            definition=[{"name": "work", "action": "emit", "argument": "safe",
                         "requires_approval": True}],
        )
        self.assertIsNone(self.store_a.claim_next(worker_id="worker_a"))
        approved = self.store_a.approve_with_event(
            protected.run_id, actor_id="reviewer", session_id="session",
            expected_version=protected.version,
        )
        self.assertEqual(approved.version, 2)
        events = self.store_a.list_task_events(protected.run_id)
        self.assertEqual([event.kind for event in events], ["task.approved"])
        claim = self.store_b.claim_next(worker_id="worker_b")
        self.assertEqual(claim.run_id, protected.run_id)

    def test_worker_mutation_is_fenced_by_generation_and_database_expiry(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        self.assertEqual(claimed.run_id, self.run.run_id)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE run_id=%s", (claimed.run_id,),
            )
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store_b.append_task_event(
                claimed.run_id, kind="task.progress", actor_id="worker",
                payload={"message": "late"}, expected_version=claimed.version,
                expected_lease_owner="worker_a",
                expected_lease_generation=claimed.lease_generation,
            )

    def test_expired_worker_is_fenced_after_atomic_recovery(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE run_id=%s", (claimed.run_id,),
            )
        recovered = self.store_b.recover_expired_with_events(actor_id="recovery", limit=10)
        recovered_run = next((run for run in recovered if run.run_id == claimed.run_id), None)
        self.assertIsNotNone(recovered_run)
        self.assertEqual(recovered_run.state, "interrupted")
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store_a.append_task_event(
                claimed.run_id, kind="task.progress", actor_id="worker",
                payload={"message": "stale worker"},
                expected_version=claimed.version,
                expected_lease_owner="worker_a",
                expected_lease_generation=claimed.lease_generation,
            )
        events = self.store_a.list_task_events(claimed.run_id)
        self.assertEqual([event.kind for event in events], ["task.interrupted"])
        self.assertFalse(events[0].payload.get("automatic_replay", True))

    def test_lease_renewal_preserves_claim_generation_and_fence(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        renewed = self.store_b.renew_lease(
            claimed.run_id, worker_id="worker_a", expected_version=claimed.version,
            lease_seconds=90,
        )
        self.assertEqual(renewed.version, claimed.version + 1)
        self.assertEqual(renewed.lease_generation, claimed.lease_generation)
        event = self.store_a.append_task_event(
            claimed.run_id, kind="task.progress", actor_id="worker",
            payload={"message": "same claim remains authorized after renewal"},
            expected_version=renewed.version, expected_lease_owner="worker_a",
            expected_lease_generation=claimed.lease_generation,
        )
        self.assertEqual(event.kind, "task.progress")

    def test_lease_fence_requires_owner_and_generation_as_a_pair(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        invalid_fences = (
            {"expected_lease_generation": claimed.lease_generation},
            {"expected_lease_owner": "worker_a"},
        )
        for fence in invalid_fences:
            with self.subTest(fence=fence):
                with self.assertRaises(ValueError):
                    self.store_a.append_task_event(
                        claimed.run_id, kind="task.progress", actor_id="worker",
                        payload={"message": "must not bypass lease expiry"},
                        expected_version=claimed.version, **fence,
                    )
                with self.assertRaises(ValueError):
                    self.store_a.transition_with_event(
                        claimed.run_id, expected_version=claimed.version,
                        to_state="succeeded", event_kind="task.completed",
                        actor_id="worker", event_payload={"ok": True}, **fence,
                    )
        self.assertEqual(self.store_a.get(claimed.run_id).state, "running")
        self.assertEqual(self.store_a.list_task_events(claimed.run_id), [])

    def test_transition_and_terminal_event_share_one_versioned_transaction(self):
        claimed = self.store_a.claim_next(worker_id="worker_a")
        completed = self.store_a.transition_with_event(
            claimed.run_id, expected_version=claimed.version, to_state="succeeded",
            event_kind="task.completed", actor_id="service",
            event_payload={"state_version": claimed.version + 1},
            result={"ok": True}, expected_lease_owner="worker_a",
            expected_lease_generation=claimed.lease_generation,
        )
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual(completed.version, claimed.version + 2)
        self.assertEqual([event.kind for event in self.store_a.list_task_events(claimed.run_id)],
                         ["task.completed"])
        self.assertIsNone(completed.lease_owner)


if __name__ == "__main__":
    unittest.main()
