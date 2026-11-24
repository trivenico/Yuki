"""PostgreSQL integration tests for external-operation idempotency and recovery."""
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


class FakeExternalProvider:
    """Deterministic test-only provider with explicit idempotency and crash points."""

    def __init__(self):
        import hashlib
        self._accepted = {}
        self.dispatch_calls = 0
        self._hashlib = hashlib

    def dispatch(self, *, idempotency_key: str, request_digest: str,
                 outcome: str = "accepted"):
        self.dispatch_calls += 1
        existing = self._accepted.get(idempotency_key)
        if existing is not None and existing["request_digest"] != request_digest:
            raise ValueError("provider_idempotency_key_payload_conflict")
        if existing is None:
            reference = self._hashlib.sha256(
                ("fake-provider:" + idempotency_key + ":" + request_digest).encode("utf-8")
            ).hexdigest()[:24]
            result_digest = self._hashlib.sha256(
                (request_digest + ":" + reference).encode("utf-8")
            ).hexdigest()
            existing = {
                "request_digest": request_digest,
                "provider_reference": reference,
                "result_digest": result_digest,
            }
            self._accepted[idempotency_key] = existing
        if outcome == "timeout_after_acceptance":
            raise TimeoutError("simulated_timeout_after_provider_acceptance")
        if outcome == "crash_after_acceptance":
            raise RuntimeError("simulated_worker_crash_after_provider_acceptance")
        if outcome != "accepted":
            raise ValueError("fake_provider_outcome_invalid")
        return dict(existing)

    def lookup(self, idempotency_key: str):
        value = self._accepted.get(idempotency_key)
        return dict(value) if value is not None else None


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresExternalOperationStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from automation.postgres_external_operation_store import PostgresExternalOperationStore
        cls.store_type = PostgresExternalOperationStore
        root = Path(__file__).resolve().parents[1]
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute((root / "migrations/security/0007_workflow_runs.sql").read_text(), prepare=False)
            conn.execute((root / "migrations/security/0008_workload_delegations.sql").read_text(), prepare=False)
            conn.execute((root / "migrations/security/0009_external_operations.sql").read_text(), prepare=False)
            applied = conn.execute("SELECT 1 FROM pg_constraint WHERE conname=%s", ("workflow_runs_ownership_status_check",)).fetchone()
            if not applied:
                conn.execute((root / "migrations/security/0010_workflow_task_ownership.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute((root / "migrations/security/0011_external_operation_authorization_context.sql").read_text(encoding="utf-8"), prepare=False)

    def setUp(self):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        self.workflow_store = PostgresWorkflowRunStore(POSTGRES_URL, max_pool_size=2)
        self.store = self.store_type(POSTGRES_URL, max_pool_size=2, authorize_reconciliation=lambda actor, operation, version: actor == "operator")
        self.key = "operation-" + uuid.uuid4().hex
        self.authorities = []
        self.task = self.workflow_store.create(
            idempotency_key=self.key, workflow_name="external-operation-test",
            definition=[{"name": "intent", "action": "emit", "argument": "metadata",
                         "requires_approval": False}],
            tenant_id="tenant-test", owner_principal_id="human:test-owner",
        )

    def tearDown(self):
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("DELETE FROM external_operation_events WHERE operation_id IN (SELECT operation_id FROM external_operations WHERE task_id=%s)", (self.task.run_id,))
            conn.execute("DELETE FROM external_operations WHERE task_id=%s", (self.task.run_id,))
            conn.execute("DELETE FROM workload_delegation_events WHERE task_id=%s", (self.task.run_id,))
            conn.execute("DELETE FROM workload_delegations WHERE task_id=%s", (self.task.run_id,))
            conn.execute("DELETE FROM workflow_runs WHERE run_id=%s", (self.task.run_id,))
        for authority in self.authorities:
            authority.close()
        self.store.close()
        self.workflow_store.close()

    def authorized_service(self, *, actions=("calendar.create",)):
        import time
        from automation.authorized_external_action_service import AuthorizedExternalActionService
        from automation.task_runtime import TaskRuntime
        from core.authorization import AuthorizationDecision, Principal
        from core.authorization_gateway import AuthorizationGateway
        from core.postgres_delegation_authority import PostgresDelegationAuthority
        from core.workload_identity import WorkloadCredentialClaims

        worker = "worker-authorized"
        claimed = self.workflow_store.claim_next(worker_id=worker)
        self.assertIsNotNone(claimed)
        now = int(time.time())
        authority = PostgresDelegationAuthority(POSTGRES_URL)
        self.authorities.append(authority)
        grant = authority.create_grant(
            grantor_id="human:test-owner", grantee_id=worker,
            tenant_id="tenant-test", agent_id="agent-1",
            task_id=self.task.run_id, lease_generation=claimed.lease_generation,
            scopes=frozenset(actions), expires_at=now + 240, now=now,
        )
        claims = WorkloadCredentialClaims(
            issuer="https://issuer.yuki.internal", subject=worker,
            tenant_id="tenant-test", agent_id="agent-1",
            audience="yuki-external-action", issued_at=now, not_before=now,
            expires_at=now + 180, token_id="token-" + uuid.uuid4().hex,
            task_id=self.task.run_id, lease_generation=claimed.lease_generation,
            scopes=frozenset(actions), delegation_id=grant.delegation_id,
        )

        class Verifier:
            def verify(self, credential, **kwargs):
                if credential != "opaque-test-credential":
                    raise PermissionError("test_credential_invalid")
                return claims

        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "decision-test", "test-policy", "allowed")

        gateway = AuthorizationGateway(Policy(), audit=lambda request, decision: True)
        runtime = TaskRuntime(
            self.workflow_store, authorization_gateway=gateway,
            principal_for_actor=lambda actor: Principal(
                principal_id=actor, kind="human", tenant_id="tenant-test",
            ),
            policy_revision="test-policy",
            workload_credential_verifier=Verifier(),
            workload_audience="yuki-external-action",
            credential_clock=lambda: now,
            delegation_authority=authority,
            require_workload_delegation=True,
        )
        service = AuthorizedExternalActionService(runtime, self.store)
        return service, authority, grant, claimed, worker

    def create_operation(self, key=None, input_digest=None):
        return self.store._create_unchecked(
            tenant_id="tenant-test", task_id=self.task.run_id, step_id="step-1",
            action_name="calendar.create", action_version="v1",
            idempotency_key=key or self.key,
            input_digest=input_digest or "a" * 64,
        )

    def test_service_rejects_split_database_security_repositories(self):
        service, authority, grant, claimed, worker = self.authorized_service()
        from automation.authorized_external_action_service import AuthorizedExternalActionService
        from automation.postgres_external_operation_store import PostgresExternalOperationStore

        mismatched = object.__new__(PostgresExternalOperationStore)
        mismatched.database_identity = "postgres:another-database"
        with self.assertRaisesRegex(ValueError, "authorized_external_action_repositories_must_share_database"):
            AuthorizedExternalActionService(service.task_runtime, mismatched)

    def test_authorized_intent_is_atomic_and_binds_identity_metadata(self):
        service, authority, grant, claimed, worker = self.authorized_service()
        operation = service.create_intent(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", step_id="step-1",
            action_name="calendar.create", action_version="v1",
            idempotency_key=self.key + "-authorized",
            input_digest="a" * 64,
        )
        duplicate = service.create_intent(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", step_id="step-1",
            action_name="calendar.create", action_version="v1",
            idempotency_key=self.key + "-authorized",
            input_digest="a" * 64,
        )
        self.assertEqual(operation.operation_id, duplicate.operation_id)
        self.assertEqual(operation.agent_id, "agent-1")
        self.assertEqual(operation.task_lease_generation, claimed.lease_generation)
        self.assertEqual(operation.authorization_decision_id, "decision-test")
        self.assertEqual(operation.policy_revision, "test-policy")
        events = self.store._list_events_unchecked(operation.operation_id)
        self.assertEqual([event["kind"] for event in events], ["operation.created"])
        self.assertEqual(events[0]["actor_id"], worker)
        self.assertEqual(events[0]["payload_json"]["agent_id"], "agent-1")
        self.assertEqual(
            service.get(task_id=self.task.run_id, worker_id=worker,
                        credential="opaque-test-credential", action_name="calendar.create",
                        operation_id=operation.operation_id).operation_id,
            operation.operation_id,
        )
        self.assertEqual(
            [event["kind"] for event in service.list_events(
                task_id=self.task.run_id, worker_id=worker,
                credential="opaque-test-credential", action_name="calendar.create",
                operation_id=operation.operation_id,
            )],
            ["operation.created"],
        )

    def test_authorized_claims_are_scoped_to_exact_action(self):
        service, authority, grant, claimed, worker = self.authorized_service(
            actions=("calendar.create", "calendar.delete"),
        )
        created = service.create_intent(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", step_id="step-create",
            action_name="calendar.create", action_version="v1",
            idempotency_key=self.key + "-create", input_digest="b" * 64,
        )
        deleted = service.create_intent(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", step_id="step-delete",
            action_name="calendar.delete", action_version="v1",
            idempotency_key=self.key + "-delete", input_digest="c" * 64,
        )
        claimed_create = service.claim_next(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action_name="calendar.create",
        )
        claimed_delete = service.claim_next(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action_name="calendar.delete",
        )
        self.assertEqual(claimed_create.operation_id, created.operation_id)
        self.assertEqual(claimed_delete.operation_id, deleted.operation_id)
        authorized = service.authorize_dispatch(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action_name="calendar.create",
            operation_id=claimed_create.operation_id,
            operation_lease_generation=claimed_create.lease_generation,
            expected_version=claimed_create.version,
        )
        self.assertEqual(authorized.operation_id, created.operation_id)
        completed = service.complete(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action_name="calendar.create",
            operation_id=created.operation_id,
            operation_lease_generation=claimed_create.lease_generation,
            expected_version=claimed_create.version,
            result_digest="8" * 64,
        )
        self.assertEqual(completed.state, "succeeded")

    def test_delegation_revocation_after_preflight_blocks_intent_transaction(self):
        service, authority, grant, claimed, worker = self.authorized_service()
        context = service.task_runtime.verify_workload_context(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action="calendar.create",
        )
        authority.revoke(grant.delegation_id, revoked_by=worker)
        with self.assertRaises(PermissionError):
            self.store.create_authorized(
                context, step_id="step-1", action_name="calendar.create",
                action_version="v1", idempotency_key=self.key + "-revoked",
                input_digest="d" * 64,
            )

    def test_task_lease_expiry_after_preflight_blocks_intent_transaction(self):
        service, authority, grant, claimed, worker = self.authorized_service()
        context = service.task_runtime.verify_workload_context(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action="calendar.create",
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                (self.task.run_id,),
            )
        with self.assertRaises(PermissionError):
            self.store.create_authorized(
                context, step_id="step-1", action_name="calendar.create",
                action_version="v1", idempotency_key=self.key + "-expired",
                input_digest="e" * 64,
            )

    def test_task_lease_generation_change_blocks_intent_transaction(self):
        service, authority, grant, claimed, worker = self.authorized_service()
        context = service.task_runtime.verify_workload_context(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action="calendar.create",
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET lease_generation=lease_generation+1 WHERE run_id=%s",
                (self.task.run_id,),
            )
        with self.assertRaises(PermissionError):
            self.store.create_authorized(
                context, step_id="step-1", action_name="calendar.create",
                action_version="v1", idempotency_key=self.key + "-generation-changed",
                input_digest="7" * 64,
            )

    def test_task_lease_change_blocks_authorized_completion(self):
        service, authority, grant, claimed, worker = self.authorized_service()
        context = service.task_runtime.verify_workload_context(
            task_id=self.task.run_id, worker_id=worker,
            credential="opaque-test-credential", action="calendar.create",
        )
        operation = self.store.create_authorized(
            context, step_id="step-1", action_name="calendar.create",
            action_version="v1", idempotency_key=self.key + "-completion",
            input_digest="f" * 64,
        )
        claimed_operation = self.store.claim_next_authorized(context)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE workflow_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s",
                (self.task.run_id,),
            )
        with self.assertRaises(PermissionError):
            self.store.complete_authorized(
                context, operation.operation_id,
                lease_generation=claimed_operation.lease_generation,
                expected_version=claimed_operation.version,
                result_digest="9" * 64,
            )

    def test_unscoped_operation_entrypoints_are_not_public(self):
        for name in ("create", "claim_next", "complete", "fail_terminal", "mark_unknown", "get", "list_events"):
            self.assertFalse(callable(getattr(self.store, name, None)), name)

    def test_operation_tenant_must_match_owned_parent_task(self):
        with self.assertRaises(psycopg.IntegrityError):
            self.store._create_unchecked(
                tenant_id="tenant-other", task_id=self.task.run_id, step_id="step-1",
                action_name="calendar.create", action_version="v1",
                idempotency_key="wrong-tenant-" + uuid.uuid4().hex,
                input_digest="a" * 64,
            )

    def test_duplicate_intent_is_idempotent_and_conflict_is_rejected(self):
        from automation.workflow_store import WorkflowConflict
        first = self.create_operation()
        duplicate = self.create_operation()
        self.assertEqual(first.operation_id, duplicate.operation_id)
        self.assertEqual(len(self.store._list_events_unchecked(first.operation_id)), 1)
        with self.assertRaises(WorkflowConflict):
            self.create_operation(input_digest="b" * 64)

    def test_claim_and_success_are_fenced_and_atomic_with_events(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        self.assertEqual(claimed.operation_id, operation.operation_id)
        completed = self.store._complete_unchecked(
            claimed.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, result_digest="c" * 64,
            provider_reference="provider-op-123",
        )
        self.assertEqual(completed.state, "succeeded")
        self.assertEqual([e["kind"] for e in self.store._list_events_unchecked(operation.operation_id)],
                         ["operation.created", "operation.claimed",
                          "operation.succeeded"])

    def test_expired_claim_becomes_unknown_and_is_not_replayed(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE external_operations SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE operation_id=%s",
                (operation.operation_id,),
            )
        self.assertIsNone(self.store._claim_next_unscoped(worker_id="worker-b"))
        unknown = self.store._get_unchecked(operation.operation_id)
        self.assertEqual(unknown.state, "unknown")
        from automation.workflow_store import WorkflowConflict
        with self.assertRaises(WorkflowConflict):
            self.store._complete_unchecked(
                operation.operation_id, worker_id="worker-a",
                lease_generation=claimed.lease_generation,
                expected_version=claimed.version, result_digest="d" * 64,
            )
        events = self.store._list_events_unchecked(operation.operation_id)
        self.assertEqual(events[-1]["kind"], "operation.unknown")
        self.assertFalse(events[-1]["payload_json"]["automatic_replay"])

    def test_explicit_reconciliation_never_requeues(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        unknown = self.store._mark_unknown_unchecked(
            operation.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, reason_code="provider_timeout",
        )
        reconciled = self.store.record_reconciliation(
            operation.operation_id, actor_id="operator",
            expected_version=unknown.version, outcome="succeeded",
            result_digest="e" * 64, provider_reference="provider-confirmed-9",
        )
        self.assertEqual(reconciled.state, "succeeded")
        self.assertIsNone(self.store._claim_next_unscoped(worker_id="worker-b"))

    def test_reconciliation_fails_closed_without_authorized_operator(self):
        operation = self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        unknown = self.store._mark_unknown_unchecked(
            operation.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, reason_code="provider_timeout",
        )
        with self.assertRaises(PermissionError):
            self.store.record_reconciliation(
                operation.operation_id, actor_id="intruder",
                expected_version=unknown.version, outcome="succeeded",
                result_digest="f" * 64,
            )

    def test_terminal_failure_cannot_be_claimed_again(self):
        self.create_operation()
        claimed = self.store._claim_next_unscoped(worker_id="worker-a")
        failed = self.store._fail_terminal_unchecked(
            claimed.operation_id, worker_id="worker-a",
            lease_generation=claimed.lease_generation,
            expected_version=claimed.version, error_code="provider_rejected",
        )
        self.assertEqual(failed.state, "failed_terminal")
        self.assertIsNone(self.store._claim_next_unscoped(worker_id="worker-b"))


if __name__ == "__main__":
    unittest.main()
