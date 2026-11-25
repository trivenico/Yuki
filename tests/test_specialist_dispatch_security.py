from __future__ import annotations

import unittest

from automation.specialist_dispatch_security import (
    authorize_specialist_dispatch,
    resolve_human_dispatch_owner,
    resolve_specialist_workload_principal,
)
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway


class Policy:
    def __init__(self, allowed=True, revision="policy-v1"):
        self.allowed = allowed
        self.revision = revision
        self.requests = []

    def authorize(self, request):
        self.requests.append(request)
        return AuthorizationDecision(
            self.allowed, "decision-1", self.revision,
            "allowed" if self.allowed else "scope_denied",
        )


class SpecialistDispatchSecurityTests(unittest.TestCase):
    def test_resolves_exact_human_owner_and_tenant(self):
        expected = Principal("owner-1", "human", tenant_id="tenant-1")
        result = resolve_human_dispatch_owner(
            lambda principal_id: expected,
            tenant_id="tenant-1", owner_principal_id="owner-1",
        )
        self.assertIs(result, expected)

    def test_rejects_owner_scope_kind_and_agent_mismatch(self):
        cases = (
            Principal("other", "human", tenant_id="tenant-1"),
            Principal("owner-1", "human", tenant_id="tenant-2"),
            Principal("owner-1", "service", tenant_id="tenant-1"),
            Principal("owner-1", "human", tenant_id="tenant-1", agent_id="agent-1"),
        )
        for principal in cases:
            with self.subTest(principal=principal):
                with self.assertRaises(PermissionError):
                    resolve_human_dispatch_owner(
                        lambda _: principal,
                        tenant_id="tenant-1", owner_principal_id="owner-1",
                    )

    def test_resolver_exception_fails_closed(self):
        def unavailable(_):
            raise ConnectionError("identity database unavailable")
        with self.assertRaisesRegex(PermissionError, "owner_resolution_failed"):
            resolve_human_dispatch_owner(
                unavailable, tenant_id="tenant-1", owner_principal_id="owner-1",
            )

    def test_resolves_exact_workload_agent_and_tenant(self):
        expected = Principal(
            "workload-agent-1", "workload",
            tenant_id="tenant-1", agent_id="agent-1",
        )
        self.assertIs(
            resolve_specialist_workload_principal(
                lambda _: expected, tenant_id="tenant-1", agent_id="agent-1",
            ),
            expected,
        )

    def test_rejects_workload_principal_scope_mismatch(self):
        for principal in (
            Principal("workload-agent-1", "workload", tenant_id="tenant-2", agent_id="agent-1"),
            Principal("workload-agent-1", "workload", tenant_id="tenant-1", agent_id="agent-2"),
            Principal("owner-1", "human", tenant_id="tenant-1"),
        ):
            with self.subTest(principal=principal):
                with self.assertRaises(PermissionError):
                    resolve_specialist_workload_principal(
                        lambda _: principal, tenant_id="tenant-1", agent_id="agent-1",
                    )

    def test_dispatch_authorization_is_audited_and_resource_scoped(self):
        policy = Policy()
        audit = []
        gateway = AuthorizationGateway(
            policy, audit=lambda request, decision: audit.append((request, decision)),
        )
        owner = Principal("owner-1", "human", tenant_id="tenant-1")
        decision = authorize_specialist_dispatch(
            gateway, owner=owner, tenant_id="tenant-1", plan_id="plan-1", policy_revision="policy-v1",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(len(audit), 1)
        request = policy.requests[0]
        self.assertEqual(request.action, "agent:dispatch")
        self.assertEqual(request.resource, "agent-plan:plan-1")
        self.assertEqual(request.risk, "high")
        self.assertEqual(request.expected_policy_revision, "policy-v1")
        self.assertIs(audit[0][0], request)

    def test_denial_stale_policy_and_audit_failure_block_dispatch(self):
        owner = Principal("owner-1", "human", tenant_id="tenant-1")
        denied = AuthorizationGateway(Policy(False), audit=lambda *_: None)
        with self.assertRaises(PermissionError):
            authorize_specialist_dispatch(
                denied, owner=owner, plan_id="plan-1", policy_revision="policy-v1",
            )
        stale = AuthorizationGateway(
            Policy(True, "policy-v0"), audit=lambda *_: None,
        )
        with self.assertRaises(PermissionError):
            authorize_specialist_dispatch(
                stale, owner=owner, plan_id="plan-1", policy_revision="policy-v1",
            )
        audit_down = AuthorizationGateway(
            Policy(True), audit=lambda *_: (_ for _ in ()).throw(OSError("audit down")),
        )
        with self.assertRaises(PermissionError):
            authorize_specialist_dispatch(
                audit_down, owner=owner, plan_id="plan-1", policy_revision="policy-v1",
            )

    def test_dispatch_authorization_rejects_owner_from_another_tenant(self):
        policy = Policy()
        gateway = AuthorizationGateway(policy, audit=lambda *_: None)
        owner = Principal("owner-1", "human", tenant_id="tenant-2")
        with self.assertRaises(PermissionError):
            authorize_specialist_dispatch(
                gateway, owner=owner, tenant_id="tenant-1",
                plan_id="plan-1", policy_revision="policy-v1",
            )
        self.assertEqual(policy.requests, [])

    def test_rejects_invalid_scope_text(self):
        resolver = lambda _: Principal("owner-1", "human", tenant_id="tenant-1")
        for tenant, owner in (("", "owner-1"), ("tenant-1", " owner-1"), ("tenant-1", "x\x00y")):
            with self.subTest(tenant=tenant, owner=owner):
                with self.assertRaises(ValueError):
                    resolve_human_dispatch_owner(
                        resolver, tenant_id=tenant, owner_principal_id=owner,
                    )


if __name__ == "__main__":
    unittest.main()
