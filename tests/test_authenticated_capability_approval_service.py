import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.auth import AuthenticationError
from core.authenticated_capability_approval_service import AuthenticatedCapabilityApprovalService
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.durable_task_engine import DurableTaskEngine
from core.tool_policy import Decision, PolicyRule, Risk, ToolPolicyEngine


class FakeAuth:
    def __init__(self):
        self.sessions = {
            "approver-session": SimpleNamespace(user_id="approver-1", permissions=frozenset({"workflow:approve", "workflow:read"})),
            "reader-session": SimpleNamespace(user_id="reader-1", permissions=frozenset({"workflow:read"})),
            "owner-session": SimpleNamespace(user_id="alice", permissions=frozenset({"workflow:approve", "workflow:read"})),
            "requester-session": SimpleNamespace(user_id="alice", permissions=frozenset({"workflow:read"})),
        }

    def resolve_session(self, token):
        return self.sessions.get(token)


class FakeSkill:
    name = "write_demo"
    risk = "low"


class FakeRegistry:
    def get(self, name):
        return FakeSkill() if name == "write_demo" else None


class AuthenticatedCapabilityApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        policy = ToolPolicyEngine((
            PolicyRule(
                "approval-required", Decision.REQUIRE_APPROVAL,
                capability="skill.invoke", principal_id="alice",
                trigger="durable_task", target="write_demo", risk=Risk.LOW,
            ),
        ), policy_version="policy-v1")
        self.gateway = CapabilityApprovalGateway(
            root / "approval.sqlite", policy,
            authorize_approver=lambda actor, request: actor == "approver-1",
        )
        self.engine = DurableTaskEngine(root / "tasks.sqlite")
        self.addCleanup(self.engine.close)
        self.goal_id = self.engine.create_goal(owner_id="alice")
        self.task = self.engine.submit(
            self.goal_id, "skill.invoke",
            {"skill_name": "write_demo", "argument": "safe-value"},
            idempotency_key="write-demo", owner_id="alice", max_attempts=1,
        )
        self.auth = FakeAuth()
        self.service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, task_engine=self.engine,
            skill_registry=FakeRegistry(),
        )

    def test_request_is_reconstructed_from_persisted_owned_task(self):
        request_id = self.service.request_approval("requester-session", self.task["id"])
        self.assertEqual(len(self.service.pending_requests("approver-session")), 1)
        self.assertIsNone(self.engine.claim("worker-before-approval", owner_id="alice"))
        delivered = []
        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, task_engine=self.engine,
            skill_registry=FakeRegistry(),
            deliver_credential=lambda rid, token: delivered.append((rid, token)),
        )
        service.approve_pending("approver-session", request_id)
        request = self.service._request_from_owned_task("alice", self.task["id"])
        decision = self.gateway.authorize(request, approval_token=delivered[0][1])
        self.assertTrue(decision.allowed)
        claimed = self.engine.claim("worker-after-approval", owner_id="alice")
        self.assertEqual(claimed["id"], self.task["id"])

    def test_unauthenticated_user_cannot_request_or_revoke(self):
        with self.assertRaises(AuthenticationError):
            self.service.request_approval("invalid-session", self.task["id"])
        with self.assertRaises(AuthenticationError):
            self.service.revoke("invalid-session", "x" * 40)

    def test_read_only_user_cannot_approve_or_revoke(self):
        with self.assertRaises(PermissionError):
            self.service.pending_requests("reader-session")
        token = self.gateway.issue_approval(
            self.service._request_from_owned_task("alice", self.task["id"]),
            approved_by="approver-1",
        )
        with self.assertRaises(PermissionError):
            self.service.revoke("reader-session", token)

    def test_audit_requires_authenticated_read_permission(self):
        with self.assertRaises(AuthenticationError):
            self.service.audit_events("invalid-session")
        self.assertEqual(self.service.audit_events("reader-session"), ())

    def test_approval_credential_is_not_returned_by_audit(self):
        delivered = []
        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, task_engine=self.engine,
            skill_registry=FakeRegistry(),
            deliver_credential=lambda rid, token: delivered.append(token),
        )
        request_id = service.request_approval("requester-session", self.task["id"])
        service.approve_pending("approver-session", request_id)
        audit = repr(service.audit_events("reader-session"))
        self.assertNotIn(delivered[0], audit)

    def test_inbox_rejects_cross_principal_task(self):
        with self.assertRaises(PermissionError):
            self.service.request_approval("approver-session", self.task["id"])

    def test_delivery_failure_revokes_credential(self):
        delivered = []

        def failing_delivery(request_id, token):
            delivered.append(token)
            raise RuntimeError("sensitive callback detail")

        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, task_engine=self.engine,
            skill_registry=FakeRegistry(), deliver_credential=failing_delivery,
        )
        request_id = service.request_approval("requester-session", self.task["id"])
        with self.assertRaisesRegex(RuntimeError, "delivery failed"):
            service.approve_pending("approver-session", request_id)
        request = self.service._request_from_owned_task("alice", self.task["id"])
        self.assertFalse(self.gateway.authorize(
            request, approval_token=delivered[0],
        ).allowed)

    def test_approval_requires_configured_protected_delivery(self):
        request_id = self.service.request_approval("requester-session", self.task["id"])
        with self.assertRaisesRegex(RuntimeError, "delivery is not configured"):
            self.service.approve_pending("approver-session", request_id)

    def test_cannot_request_for_running_or_terminal_task(self):
        claimed = self.engine.claim("test-worker", owner_id="alice")
        with self.assertRaises(PermissionError):
            self.service.request_approval("requester-session", claimed["id"])
        self.engine.fail(claimed["id"], "test-worker", "test", retry_delay_seconds=0)
        # Single attempt means the failed task is terminal.
        with self.assertRaises(PermissionError):
            self.service.request_approval("requester-session", claimed["id"])

    def test_approval_rechecks_task_state_after_request(self):
        request_id = self.service.request_approval("requester-session", self.task["id"])
        self.engine.cancel_task(self.task["id"])
        delivered = []
        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, task_engine=self.engine,
            skill_registry=FakeRegistry(),
            deliver_credential=lambda rid, token: delivered.append(token),
        )
        with self.assertRaisesRegex(PermissionError, "only queued tasks"):
            service.approve_pending("approver-session", request_id)
        self.assertEqual(delivered, [])

    def test_worker_cannot_claim_task_while_approval_is_pending(self):
        request_id = self.service.request_approval("requester-session", self.task["id"])
        self.assertIsNone(self.engine.claim("worker-1", owner_id="alice"))
        self.assertTrue(self.engine.release_task_approval_hold(self.task["id"], request_id))
        claimed = self.engine.claim("worker-1", owner_id="alice")
        self.assertEqual(claimed["id"], self.task["id"])

    def test_approval_rechecks_goal_state_after_request(self):
        request_id = self.service.request_approval("requester-session", self.task["id"])
        self.engine.set_goal_state(self.goal_id, "paused")
        delivered = []
        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, task_engine=self.engine,
            skill_registry=FakeRegistry(),
            deliver_credential=lambda rid, token: delivered.append(token),
        )
        with self.assertRaisesRegex(PermissionError, "goal is not owned"):
            service.approve_pending("approver-session", request_id)
        self.assertEqual(delivered, [])

    def test_cannot_request_for_task_with_untrusted_skill_or_payload(self):
        class EmptyRegistry:
            def get(self, name):
                return None
        service = AuthenticatedCapabilityApprovalService(
            self.auth, self.gateway, task_engine=self.engine,
            skill_registry=EmptyRegistry(),
        )
        with self.assertRaises(PermissionError):
            service.request_approval("requester-session", self.task["id"])


if __name__ == "__main__":
    unittest.main()
