from __future__ import annotations

from dataclasses import dataclass
import tempfile
from pathlib import Path

import pytest

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.workload_identity import WorkloadCredentialClaims


@dataclass(frozen=True)
class Claims:
    subject: str
    task_id: str
    lease_generation: str
    audience: str
    scopes: frozenset[str]


class TokenVerifier:
    def verify(self, token, *, expected_audience, expected_task_id,
               expected_lease_generation, now):
        parts = token.split("|")
        if len(parts) != 3 or parts[1] != expected_lease_generation:
            raise PermissionError("stale_or_invalid_credential")
        return WorkloadCredentialClaims(
            issuer="https://issuer.test", subject=parts[0], tenant_id="tenant-a",
            agent_id="agent-a", audience=expected_audience, issued_at=now - 1,
            not_before=now - 1, expires_at=now + 100, token_id="token-test",
            task_id=expected_task_id, lease_generation=expected_lease_generation,
            scopes=frozenset(parts[2].split(",")),
        )


class WorkloadTaskBindingTests:
    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "binding.sqlite3"))
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "supervisor" and action in {
                "task:create", "task:progress", "task:artifact",
                "task:complete", "task:fail", "task:lease:renew", "task:read",
            },
            workload_credential_verifier=TokenVerifier(),
            workload_audience="yuki-task-runtime",
        )
        self.task = self.runtime.enqueue(
            actor_id="supervisor", idempotency_key="binding-1", name="binding",
            steps=[{"name": "run", "action": "emit", "argument": "work",
                    "requires_approval": False}],
        )
        self.claimed = self.store.claim_next(worker_id="worker-a")

    def teardown_method(self):
        self.tmp.cleanup()

    def token(self, scopes="task:progress", worker="worker-a", generation=None):
        return f"{worker}|{generation or self.claimed.lease_generation}|{scopes}"

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
