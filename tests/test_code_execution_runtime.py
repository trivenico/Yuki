"""Composition tests for the trusted code-execution control plane."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore, WorkflowTransitionError
from core.approval_audit import SQLiteApprovalAuditLog
from core.code_execution import CodeExecutionContractError, CodeExecutionRequest
from core.code_execution_runtime import (
    CodeExecutionAuditRecorder,
    build_code_execution_runtime,
    build_task_bound_code_execution_runtime,
    derive_execution_grant_key,
    TaskRuntimeExecutionPolicy,
)


class _KeyProvider:
    def hmac_key(self):
        return b"stable-test-os-key-store-master-key-32"


class _Executor:
    def execute(self, request, grant, *, dispatch_tool, cancellation_requested):
        raise AssertionError("runtime composition test must not execute generated code")


class CodeExecutionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.grants = str(root / "grants.sqlite3")
        self.request = CodeExecutionRequest(
            execution_id="runtime-execution-01",
            actor_id="account-user-01",
            session_id="session-01",
            task_id="task-01",
            source="result = 42",
            tool_names=(),
            max_tool_calls=0,
        )

    def _approved_running_task(self):
        store = WorkflowRunStore(str(Path(self.temp.name) / "tasks.sqlite3"))
        permissions = {"task:create", "task:read", "task:approve", "task:cancel"}
        task_runtime = TaskRuntime(
            store,
            authorize=lambda actor, action, task_id: (
                actor in {"account-user-01", "account-user-02"} and action in permissions
            ),
        )
        task = task_runtime.enqueue(
            actor_id="account-user-01",
            idempotency_key="execution-task-01",
            name="code-execution",
            steps=[{"name": "run", "action": "emit", "argument": "bounded",
                    "requires_approval": True}],
        )
        raw_session_token = "authenticated-session-01"
        session_fingerprint = hashlib.sha256(raw_session_token.encode("utf-8")).hexdigest()
        approved = task_runtime.approve(
            actor_id="account-user-01", task_id=task.task_id,
            session_id=raw_session_token, expected_version=task.version,
        )
        claimed = store.claim_next(worker_id="trusted-worker-01")
        self.assertEqual(claimed.run_id, approved.task_id)
        request = CodeExecutionRequest(
            execution_id="task-bound-execution-01",
            actor_id="account-user-01",
            session_id=session_fingerprint,
            task_id=task.task_id,
            source="result = 42",
            tool_names=(),
            max_tool_calls=0,
        )
        return task_runtime, request

    def test_task_policy_requires_approved_running_lease_and_exact_session(self):
        task_runtime, request = self._approved_running_task()
        policy = TaskRuntimeExecutionPolicy(
            task_runtime,
            worker_id="trusted-worker-01",
            authenticated_context=lambda candidate: (
                candidate.actor_id == request.actor_id
                and candidate.session_id == request.session_id
            ),
        )
        self.assertTrue(policy(request))
        wrong_session = CodeExecutionRequest(
            execution_id=request.execution_id,
            actor_id=request.actor_id,
            session_id=hashlib.sha256(b"different-session").hexdigest(),
            task_id=request.task_id,
            source=request.source,
            tool_names=(),
            max_tool_calls=0,
        )
        self.assertFalse(policy(wrong_session))
        self.assertFalse(TaskRuntimeExecutionPolicy(
            task_runtime,
            worker_id="different-worker",
            authenticated_context=lambda candidate: True,
        )(request))
        other_account = CodeExecutionRequest(
            execution_id=request.execution_id,
            actor_id="account-user-02",
            session_id=request.session_id,
            task_id=request.task_id,
            source=request.source,
            tool_names=(),
            max_tool_calls=0,
        )
        # Even a broad read capability cannot override immutable task ownership.
        self.assertFalse(policy(other_account))
        with self.assertRaises(WorkflowTransitionError):
            task_runtime.store.execute_if_current_lease(
                request.task_id,
                actor_id="account-user-02",
                session_id=request.session_id,
                worker_id="trusted-worker-01",
                operation=lambda: {"should_not_run": True},
            )


    def test_task_policy_rejects_spoofed_actor_or_session_before_store_lookup(self):
        task_runtime, request = self._approved_running_task()
        authenticated = {"actor_id": request.actor_id, "session_id": request.session_id}
        policy = TaskRuntimeExecutionPolicy(
            task_runtime,
            worker_id="trusted-worker-01",
            authenticated_context=lambda candidate: (
                candidate.actor_id == authenticated["actor_id"]
                and candidate.session_id == authenticated["session_id"]
            ),
        )
        self.assertTrue(policy(request))
        spoofed_actor = CodeExecutionRequest(
            execution_id="spoofed-actor-execution",
            actor_id="account-user-02",
            session_id=request.session_id,
            task_id=request.task_id,
            source=request.source,
            tool_names=(),
            max_tool_calls=0,
        )
        self.assertFalse(policy(spoofed_actor))
        spoofed_session = CodeExecutionRequest(
            execution_id="spoofed-session-execution",
            actor_id=request.actor_id,
            session_id=hashlib.sha256(b"attacker-session").hexdigest(),
            task_id=request.task_id,
            source=request.source,
            tool_names=(),
            max_tool_calls=0,
        )
        self.assertFalse(policy(spoofed_session))
        authenticated["session_id"] = hashlib.sha256(b"rotated-session").hexdigest()
        self.assertFalse(policy(request))


    def test_task_bound_dispatch_rechecks_authenticated_context_after_grant(self):
        task_runtime, request = self._approved_running_task()
        request = CodeExecutionRequest(
            execution_id=request.execution_id,
            actor_id=request.actor_id,
            session_id=request.session_id,
            task_id=request.task_id,
            source=request.source,
            tool_names=("approved-tool",),
            max_tool_calls=1,
        )
        session_live = {"value": True}
        runtime = build_task_bound_code_execution_runtime(
            _Executor(),
            database_path=self.grants,
            task_runtime=task_runtime,
            worker_id="trusted-worker-01",
            authenticated_context=lambda candidate: (
                session_live["value"]
                and candidate.actor_id == request.actor_id
                and candidate.session_id == request.session_id
            ),
            audit_log=self.audit,
            key_provider=_KeyProvider(),
        )
        grant = runtime.grant_authority.issue(request)
        side_effects = []
        session_live["value"] = False
        with self.assertRaisesRegex(
            CodeExecutionContractError, "no longer active"
        ):
            runtime.grant_authority.authorize_tool_call(
                grant,
                request,
                tool_name="approved-tool",
                operation=lambda: side_effects.append("executed") or {"ok": True},
            )
        self.assertEqual(side_effects, [])

    def test_task_cancellation_serializes_after_inflight_host_operation(self):
        task_runtime, request = self._approved_running_task()
        policy = TaskRuntimeExecutionPolicy(
            task_runtime,
            worker_id="trusted-worker-01",
            authenticated_context=lambda candidate: (
                candidate.actor_id == request.actor_id
                and candidate.session_id == request.session_id
            ),
        )
        operation_started = threading.Event()
        release_operation = threading.Event()
        cancellation_started = threading.Event()
        cancellation_finished = threading.Event()
        errors = []
        results = []

        def operation():
            operation_started.set()
            if not release_operation.wait(timeout=3):
                raise TimeoutError("test operation release timed out")
            return {"ok": True}

        def dispatch():
            try:
                results.append(policy.authorize_operation(request, operation))
            except Exception as exc:
                errors.append(exc)

        def cancel():
            try:
                current = task_runtime.get(
                    actor_id="account-user-01", task_id=request.task_id
                )
                cancellation_started.set()
                task_runtime.cancel(
                    actor_id="account-user-01", task_id=request.task_id,
                    expected_version=current.version,
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                cancellation_finished.set()

        dispatch_thread = threading.Thread(target=dispatch)
        dispatch_thread.start()
        self.assertTrue(operation_started.wait(timeout=2))
        cancel_thread = threading.Thread(target=cancel)
        cancel_thread.start()
        self.assertTrue(cancellation_started.wait(timeout=2))
        time.sleep(0.1)
        self.assertFalse(cancellation_finished.is_set())
        release_operation.set()
        dispatch_thread.join(timeout=3)
        cancel_thread.join(timeout=3)
        self.assertFalse(dispatch_thread.is_alive())
        self.assertFalse(cancel_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [{"ok": True}])
        self.assertFalse(policy(request))

    def test_task_policy_denies_after_task_cancellation(self):
        task_runtime, request = self._approved_running_task()
        policy = TaskRuntimeExecutionPolicy(
            task_runtime, worker_id="trusted-worker-01"
        )
        self.assertTrue(policy(request))
        current = task_runtime.get(
            actor_id="account-user-01", task_id=request.task_id
        )
        task_runtime.cancel(
            actor_id="account-user-01", task_id=request.task_id,
            expected_version=current.version,
        )
        self.assertFalse(policy(request))

    def test_task_bound_composition_issues_only_for_live_approved_task(self):
        task_runtime, request = self._approved_running_task()
        runtime = build_task_bound_code_execution_runtime(
            _Executor(),
            database_path=self.grants,
            task_runtime=task_runtime,
            worker_id="trusted-worker-01",
            authenticated_context=lambda candidate: (
                candidate.actor_id == request.actor_id
                and candidate.session_id == request.session_id
            ),
            audit_log=self.audit,
            key_provider=_KeyProvider(),
        )
        grant = runtime.grant_authority.issue(request)
        self.assertTrue(runtime.grant_authority.verify(grant, request))
        task_runtime.cancel(
            actor_id="account-user-01", task_id=request.task_id,
            expected_version=task_runtime.get(
                actor_id="account-user-01", task_id=request.task_id
            ).version,
        )
        with self.assertRaisesRegex(ValueError, "not approved"):
            runtime.grant_authority.issue(CodeExecutionRequest(
                execution_id="task-bound-execution-02",
                actor_id=request.actor_id,
                session_id=request.session_id,
                task_id=request.task_id,
                source=request.source,
                tool_names=(),
                max_tool_calls=0,
            ))

    def test_key_derivation_is_stable_and_domain_separated(self):
        master = _KeyProvider().hmac_key()
        first = derive_execution_grant_key(master)
        self.assertEqual(first, derive_execution_grant_key(master))
        self.assertNotEqual(first, master)
        self.assertNotEqual(first, derive_execution_grant_key(master + b"x"))

    def test_runtime_uses_injected_os_key_provider_and_live_policy(self):
        authorized = []
        runtime = build_code_execution_runtime(
            _Executor(),
            database_path=self.grants,
            authorize_request=lambda request: authorized.append(request.actor_id) or True,
            audit_log=self.audit,
            key_provider=_KeyProvider(),
        )
        grant = runtime.grant_authority.issue(self.request)
        self.assertTrue(runtime.grant_authority.verify(grant, self.request))
        self.assertEqual(authorized, ["account-user-01"])

    def test_denied_policy_prevents_grant_issuance(self):
        runtime = build_code_execution_runtime(
            _Executor(),
            database_path=self.grants,
            authorize_request=lambda request: False,
            audit_log=self.audit,
            key_provider=_KeyProvider(),
        )
        with self.assertRaisesRegex(ValueError, "not approved"):
            runtime.grant_authority.issue(self.request)

    def test_execution_events_flow_into_existing_hash_chained_audit(self):
        recorder = CodeExecutionAuditRecorder(self.audit)
        record = {
            "actor_id": "account-user-01",
            "execution_id": "runtime-execution-01",
            "task_id": "task-01",
            "session_id": "session-01",
            "manifest_sha256": "a" * 64,
        }
        recorder("code_execution.started", record)
        recorder("code_execution.tool_requested", record)
        recorder("code_execution.tool_completed", record)
        recorder("code_execution.completed", record)
        events = list(reversed(self.audit.list_events()))
        self.assertEqual(
            [event["event_type"] for event in events],
            ["execution_started", "requested", "executed", "executed"],
        )
        self.assertTrue(self.audit.verify_chain())
        self.assertTrue(all(event["pending_id_hash"] for event in events))
        self.assertTrue(all(event["actor_hash"] for event in events))

    def test_audit_correlation_hash_accepts_maximum_length_execution_id(self):
        recorder = CodeExecutionAuditRecorder(self.audit)
        recorder("code_execution.started", {
            "actor_id": "account-user-01",
            "execution_id": "exec-" + "x" * 155,
        })
        self.assertTrue(self.audit.verify_chain())
        self.assertEqual(len(self.audit.list_events()[0]["pending_id_hash"]), 64)

    def test_runtime_refuses_a_tampered_existing_audit_chain(self):
        self.audit.append(
            event_type="requested",
            pending_id="prior-event",
            actor="account-user-01",
            skill_id="test",
        )
        with sqlite3.connect(self.audit.database_path) as db:
            db.execute(
                "UPDATE approval_audit_events SET event_hash=? WHERE event_id=1",
                ("0" * 64,),
            )
        with self.assertRaisesRegex(RuntimeError, "audit chain verification failed"):
            build_code_execution_runtime(
                _Executor(),
                database_path=self.grants,
                authorize_request=lambda request: True,
                audit_log=self.audit,
                key_provider=_KeyProvider(),
            )

    def test_unknown_audit_event_fails_closed(self):
        recorder = CodeExecutionAuditRecorder(self.audit)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            recorder("code_execution.debug_dump", {
                "actor_id": "account-user-01",
                "execution_id": "runtime-execution-01",
            })


if __name__ == "__main__":
    unittest.main()
