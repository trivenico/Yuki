from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.workload_identity import WorkloadCredentialClaims
from core.delegation_authority import DelegationAuthority


class TokenVerifier:
    def verify(self, token, *, expected_audience, expected_task_id,
               expected_lease_generation, now):
        parts = token.split("|")
        if len(parts) not in (3, 4) or parts[1] != expected_lease_generation:
            raise PermissionError("stale_or_invalid_credential")
        return WorkloadCredentialClaims(
            issuer="https://issuer.test", subject=parts[0], tenant_id="tenant-a",
            agent_id="agent-a", audience=expected_audience, issued_at=now - 1,
            not_before=now - 1, expires_at=now + 100, token_id="token-test",
            task_id=expected_task_id, lease_generation=expected_lease_generation,
            scopes=frozenset(parts[2].split(",")),
            delegation_id=parts[3] if len(parts) == 4 else None,
        )


class TestWorkloadTaskBinding:
    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "binding.sqlite3"))
        self.delegations = DelegationAuthority(str(Path(self.tmp.name) / "delegations.sqlite3"))
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "supervisor" and action in {
                "task:create", "task:progress", "task:artifact",
                "task:complete", "task:fail", "task:lease:renew", "task:read",
            },
            workload_credential_verifier=TokenVerifier(),
            workload_audience="yuki-task-runtime",
            delegation_authority=self.delegations,
        )
        self.task = self.runtime.enqueue(
            actor_id="supervisor", idempotency_key="binding-1", name="binding",
            steps=[{"name": "run", "action": "emit", "argument": "work",
                    "requires_approval": False}],
        )
        self.claimed = self.store.claim_next(worker_id="worker-a")

    def teardown_method(self):
        self.tmp.cleanup()

    def token(self, scopes="task:progress", worker="worker-a", generation=None, delegation=None):
        suffix = f"|{delegation}" if delegation else ""
        return f"{worker}|{generation or self.claimed.lease_generation}|{scopes}{suffix}"

    def delegation(self, scopes={"task:progress"}):
        now = int(time.time())
        return self.delegations.create_grant(
            grantor_id="supervisor", grantee_id="worker-a",
            tenant_id="tenant-a", agent_id="agent-a", task_id=self.task.task_id,
            scopes=scopes, expires_at=now + 300, now=now,
        )

    def progress(self, token=None):
        return self.runtime.record_progress(
            actor_id="supervisor", task_id=self.task.task_id,
            worker_id="worker-a", expected_version=self.claimed.version,
            message="working", workload_credential=token,
        )

    def test_claim_assigns_unique_lease_generation(self):
        assert self.claimed.lease_generation
        assert self.store.get(self.task.task_id).lease_generation is None

    def test_progress_requires_valid_credential_and_action_scope(self):
        with pytest.raises(PermissionError, match="workload credential is required"):
            self.progress()
        with pytest.raises(PermissionError, match="not authorized"):
            self.progress(self.token("task:complete"))
        updated = self.progress(self.token())
        assert updated.version == self.claimed.version + 1

    def test_credential_subject_must_match_lease_owner(self):
        with pytest.raises(PermissionError, match="not authorized"):
            self.progress(self.token(worker="attacker"))

    def test_lease_renewal_rotates_generation_and_rejects_old_credential(self):
        old_generation = self.claimed.lease_generation
        renewed = self.runtime.renew_lease(
            actor_id="supervisor", task_id=self.task.task_id, worker_id="worker-a",
            expected_version=self.claimed.version,
            workload_credential=self.token("task:lease:renew"),
        )
        assert renewed.lease_generation
        assert renewed.lease_generation != old_generation
        with pytest.raises(PermissionError, match="workload credential rejected"):
            self.runtime.record_progress(
                actor_id="supervisor", task_id=self.task.task_id, worker_id="worker-a",
                expected_version=renewed.version, message="stale token",
                workload_credential=self.token("task:progress", generation=old_generation),
            )
        current = self.runtime.active_lease(self.task.task_id)
        assert current.generation == renewed.lease_generation
        assert current.worker_id == "worker-a"

    def test_completion_uses_lease_generation_fence(self):
        completed = self.runtime.complete(
            actor_id="supervisor", task_id=self.task.task_id,
            expected_version=self.claimed.version, worker_id="worker-a",
            result={"ok": True}, workload_credential=self.token("task:complete"),
        )
        assert completed.state == "succeeded"
        assert self.store.get(self.task.task_id).lease_generation is None


    def test_verified_workload_principal_is_authorized_with_delegation_context(self):
        from core.authorization import AuthorizationDecision, Principal
        from core.authorization_gateway import AuthorizationGateway

        requests = []

        class Policy:
            def authorize(self, request):
                requests.append(request)
                return AuthorizationDecision(True, "decision-" + str(len(requests)),
                                             request.expected_policy_revision or "1", "allowed")

        gateway = AuthorizationGateway(Policy(), audit=lambda request, decision: None)
        self.delegation(scopes={"task:progress"})
        runtime = TaskRuntime(
            self.store,
            authorization_gateway=gateway,
            principal_for_actor=lambda actor: Principal(actor, "human"),
            policy_revision="1",
            workload_credential_verifier=TokenVerifier(),
            workload_audience="yuki-task-runtime",
        )
        runtime.record_progress(
            actor_id="supervisor", task_id=self.task.task_id, worker_id="worker-a",
            expected_version=self.claimed.version, message="delegated progress",
            workload_credential=self.token(delegation="delegation-42"),
        )
        workload_request = next(item for item in requests if item.principal.kind == "workload")
        assert workload_request.principal.principal_id == "worker-a"
        assert workload_request.principal.tenant_id == "tenant-a"
        assert workload_request.principal.agent_id == "agent-a"
        assert workload_request.delegation_id == "delegation-42"
        assert workload_request.task_id == self.task.task_id
        assert workload_request.action == "task:progress"


    def test_workload_decision_binds_persisted_approval_and_delegation(self):
        from core.authorization import AuthorizationDecision, Principal
        from core.authorization_gateway import AuthorizationGateway

        requests = []

        class Policy:
            def authorize(self, request):
                requests.append(request)
                return AuthorizationDecision(True, "decision-" + str(len(requests)),
                                             request.expected_policy_revision or "1", "allowed")

        gateway = AuthorizationGateway(Policy(), audit=lambda request, decision: None)
        runtime = TaskRuntime(
            self.store,
            authorization_gateway=gateway,
            principal_for_actor=lambda actor: Principal(actor, "human"),
            policy_revision="1",
            workload_credential_verifier=TokenVerifier(),
            workload_audience="yuki-task-runtime",
        )
        pending = runtime.enqueue(
            actor_id="supervisor", idempotency_key="approved-delegated-task",
            name="approved delegated task",
            steps=[{"name": "run", "action": "emit", "argument": "work",
                    "requires_approval": True}],
        )
        approved = runtime.approve(
            actor_id="supervisor", task_id=pending.task_id,
            session_id="session-approval", expected_version=pending.version,
        )
        assert approved.approval_id
        claimed = self.store.claim_next(worker_id="worker-a")
        assert claimed.approval_id == approved.approval_id
        runtime.record_progress(
            actor_id="supervisor", task_id=approved.task_id, worker_id="worker-a",
            expected_version=claimed.version, message="approved delegated work",
            workload_credential=self.token(
                generation=claimed.lease_generation, delegation="delegation-42",
            ),
        )
        workload_request = next(item for item in requests if item.principal.kind == "workload")
        assert workload_request.approval_id == approved.approval_id
        assert workload_request.delegation_id == "delegation-42"
        assert workload_request.task_id == approved.task_id

    def test_verified_workload_gateway_deny_blocks_task_mutation(self):
        from core.authorization import AuthorizationDecision, Principal
        from core.authorization_gateway import AuthorizationGateway

        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(
                    request.principal.kind != "workload", "decision-1",
                    request.expected_policy_revision or "1", "denied",
                )

        gateway = AuthorizationGateway(Policy(), audit=lambda request, decision: None)
        runtime = TaskRuntime(
            self.store,
            authorization_gateway=gateway,
            principal_for_actor=lambda actor: Principal(actor, "human"),
            policy_revision="1",
            workload_credential_verifier=TokenVerifier(),
            workload_audience="yuki-task-runtime",
        )
        with pytest.raises(PermissionError, match="workload principal is not authorized"):
            runtime.record_progress(
                actor_id="supervisor", task_id=self.task.task_id, worker_id="worker-a",
                expected_version=self.claimed.version, message="must be denied",
                workload_credential=self.token(delegation="delegation-42"),
            )
        assert self.store.get(self.task.task_id).version == self.claimed.version


def test_agent_task_journal_requests_fresh_credentials():
    from automation.agent_task_journal import AgentTaskJournal
    tmp = tempfile.TemporaryDirectory()
    try:
        store = WorkflowRunStore(str(Path(tmp.name) / 'journal.sqlite3'))
        runtime = TaskRuntime(store, authorize=lambda actor, action, task: actor == 'supervisor' and action in {'task:create', 'task:read', 'task:progress', 'task:lease:renew'}, workload_credential_verifier=TokenVerifier(), workload_audience='yuki-task-runtime')
        task = runtime.enqueue(actor_id='supervisor', idempotency_key='journal-binding', name='journal', steps=[{'name': 'run', 'action': 'emit', 'argument': 'work', 'requires_approval': False}])
        store.claim_next(worker_id='worker-a')
        calls = []
        def provider(action, task_id, worker_id):
            calls.append(action)
            lease = runtime.active_lease(task_id)
            return worker_id + '|' + lease.generation + '|' + action
        journal = AgentTaskJournal(runtime, actor_id='supervisor', task_id=task.task_id, worker_id='worker-a', workload_credential_provider=provider)
        journal('agent.started', {})
        journal.renew_lease(lease_seconds=120)
        journal('agent.tools_dispatched', {'count': 1})
        assert calls == ['task:progress', 'task:lease:renew', 'task:progress']
    finally:
        tmp.cleanup()
