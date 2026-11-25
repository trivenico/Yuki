from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway
from core.guarded_tool_runtime import GuardedToolRuntime


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.requests = []

    def authorize(self, request):
        self.requests.append(request)
        return AuthorizationDecision(
            self.allowed, "decision-1", "policy-v1",
            "allowed" if self.allowed else "scope_denied",
        )


class AuthorizationGatewayTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal(
            "alice", "human", tenant_id="tenant-1",
            permissions=frozenset({"task:create", "task:read"}),
        )
        self.request = AuthorizationRequest(
            self.principal, "task:create", "task:collection",
            expected_policy_revision="policy-v1",
        )

    def test_gateway_audits_allow_and_deny_decisions(self):
        policy = Policy()
        events = []
        gateway = AuthorizationGateway(policy, audit=lambda request, decision: events.append((request, decision)))
        decision = gateway.authorize(self.request)
        self.assertIs(decision.allowed, True)
        self.assertEqual(len(events), 1)
        self.assertIs(events[0][0], self.request)
        self.assertEqual(events[0][1].decision_id, "decision-1")

        denied_policy = Policy(False)
        denied_gateway = AuthorizationGateway(
            denied_policy, audit=lambda request, decision: events.append((request, decision)),
        )
        denied = denied_gateway.authorize(self.request)
        self.assertIs(denied.allowed, False)
        self.assertEqual(denied.reason_code, "scope_denied")
        self.assertEqual(len(events), 2)

    def test_independent_receipt_is_durably_recorded_before_audit(self):
        calls = []
        class Recorder:
            def record_decision(self, request, decision, *, decision_hash):
                calls.append(("receipt", request, decision, decision_hash))
                return "receipt-1"
        gateway = AuthorizationGateway(
            Policy(),
            evidence_recorder=Recorder(),
            audit=lambda request, decision: calls.append(("audit", request, decision)),
        )
        decision = gateway.authorize(self.request)
        self.assertIs(decision.allowed, True)
        self.assertEqual([item[0] for item in calls], ["receipt", "audit"])
        self.assertIs(calls[0][1], self.request)
        self.assertEqual(calls[0][3], "c8f7b3e1f0e5f8d5f3f7e0a2b1d6c4a9e3f5d7b9a1c3e5f7d9b1a3c5e7f9d1b")

    def test_missing_independent_receipt_fails_closed_before_audit(self):
        audits = []
        class Recorder:
            def record_decision(self, request, decision, *, decision_hash):
                raise ConnectionError("independent_source_unavailable")
        gateway = AuthorizationGateway(
            Policy(), evidence_recorder=Recorder(),
            audit=lambda *args: audits.append(args),
        )
        decision = gateway.authorize(self.request)
        self.assertIs(decision.allowed, False)
        self.assertEqual(decision.reason_code, "evidence_unavailable")
        self.assertEqual(audits, [])

    def test_empty_receipt_identifier_fails_closed(self):
        class Recorder:
            def record_decision(self, request, decision, *, decision_hash):
                return "  "
        gateway = AuthorizationGateway(
            Policy(), evidence_recorder=Recorder(), audit=lambda *_: None,
        )
        self.assertEqual(gateway.authorize(self.request).reason_code, "evidence_unavailable")

    def test_audit_failure_converts_allow_to_deny(self):
        gateway = AuthorizationGateway(
            Policy(), audit=lambda *_: (_ for _ in ()).throw(OSError("audit down")),
        )
        decision = gateway.authorize(self.request)
        self.assertIs(decision.allowed, False)
        self.assertEqual(decision.reason_code, "audit_unavailable")

    def test_task_runtime_uses_central_gateway_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowRunStore(str(Path(directory) / "tasks.sqlite3"))
            policy = Policy()
            events = []
            gateway = AuthorizationGateway(
                policy, audit=lambda request, decision: events.append((request.action, decision.allowed)),
            )
            runtime = TaskRuntime(
                store,
                authorization_gateway=gateway,
                principal_for_actor=lambda actor: self.principal,
                policy_revision="policy-v1",
            )
            task = runtime.enqueue(
                actor_id="alice", idempotency_key="gateway-task",
                name="gateway", steps=[{"name": "run", "action": "emit",
                                        "argument": "safe", "requires_approval": False}],
            )
            self.assertEqual(task.state, "queued")
            self.assertEqual(events, [("task:create", True)])
            self.assertEqual(policy.requests[0].resource, "task:collection")
            self.assertEqual(policy.requests[0].expected_policy_revision, "policy-v1")

    def test_task_runtime_denial_prevents_store_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowRunStore(str(Path(directory) / "denied.sqlite3"))
            gateway = AuthorizationGateway(Policy(False), audit=lambda *_: None)
            runtime = TaskRuntime(
                store, authorization_gateway=gateway,
                principal_for_actor=lambda actor: self.principal,
                policy_revision="policy-v1",
            )
            with self.assertRaises(PermissionError):
                runtime.enqueue(
                    actor_id="alice", idempotency_key="denied-task",
                    name="denied", steps=[{"name": "run", "action": "emit",
                                           "argument": "blocked", "requires_approval": False}],
                )
            self.assertEqual(store.list_runs(limit=10), [])

    def test_guarded_tool_runtime_uses_gateway_audit_before_handler(self):
        calls = []
        events = []
        gateway = AuthorizationGateway(
            Policy(), audit=lambda request, decision: events.append(
                (request.action, request.resource, decision.allowed)
            ),
        )
        runtime = GuardedToolRuntime(
            {"read": lambda value: calls.append(value) or value},
            gateway,
            principal=self.principal,
            policy_revision="policy-v1",
            task_id="task-7",
        )
        self.assertEqual(runtime.invoke("read", "payload"), "payload")
        self.assertEqual(calls, ["payload"])
        self.assertEqual(events, [("tool:invoke", "tool:read", True)])

    def test_audit_writer_false_result_converts_allow_to_deny(self):
        gateway = AuthorizationGateway(Policy(), audit=lambda *_: False)
        decision = gateway.authorize(self.request)
        self.assertIs(decision.allowed, False)
        self.assertEqual(decision.reason_code, "audit_unavailable")

    def test_gateway_requires_audit_writer(self):
        with self.assertRaises(TypeError):
            AuthorizationGateway(Policy(), audit=None)


if __name__ == "__main__":
    unittest.main()
