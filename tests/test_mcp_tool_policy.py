import unittest

from core.mcp_tool_policy import (
    MCPToolApprovalRequest,
    MCPToolPolicy,
    MCPToolPolicyError,
    MCPToolPolicyRegistry,
    MCPToolRisk,
)


class MCPToolPolicyTests(unittest.TestCase):
    def setUp(self):
        self.read = MCPToolPolicy(
            "owner", "provider", "account", "read_profile",
            MCPToolRisk.READ_ONLY, ("profile:read",), 1,
        )
        self.write = MCPToolPolicy(
            "owner", "provider", "account", "update_profile",
            MCPToolRisk.WRITE, ("profile:write",), 2,
        )
        self.financial = MCPToolPolicy(
            "owner", "provider", "account", "fund_milestone",
            MCPToolRisk.FINANCIAL, ("payments:write",), 3,
        )
        self.events = []
        self.confirmations = []
        self.registry = MCPToolPolicyRegistry(
            owner_id="owner", policies=(self.read, self.write, self.financial), revision=7,
        )

    def approver(self, *, account="account", scopes=("profile:read", "profile:write", "payments:write"),
                 confirm=True, audit=None):
        def owner_approval(request):
            self.confirmations.append(request)
            return confirm
        return self.registry.build_approver(
            owner_id="owner", provider_id="provider", account_id=account,
            granted_scopes=scopes, owner_approval=owner_approval,
            audit=audit or self.events.append,
            current_revision=lambda: self.registry.revision,
        )

    def test_exact_read_only_policy_allows_without_interactive_confirmation(self):
        approve = self.approver()
        self.assertTrue(approve("read_profile", {"user_id": "123"}))
        self.assertEqual(self.confirmations, [])
        self.assertEqual(
            [event["event"] for event in self.events],
            ["requested", "authorized_read_only"],
        )

    def test_unknown_tool_is_default_denied(self):
        approve = self.approver()
        self.assertFalse(approve("delete_everything", {}))
        self.assertEqual(self.events, [])
        self.assertEqual(self.confirmations, [])

    def test_policy_is_bound_to_exact_account(self):
        approve = self.approver(account="different-account")
        self.assertFalse(approve("read_profile", {}))
        self.assertFalse(self.events)

    def test_write_requires_fresh_literal_true_owner_confirmation(self):
        approve = self.approver(confirm=True)
        self.assertTrue(approve("update_profile", {"display_name": "Yuki"}))
        request = self.confirmations[0]
        self.assertIsInstance(request, MCPToolApprovalRequest)
        self.assertEqual(request.risk, MCPToolRisk.WRITE)
        self.assertEqual(request.policy_version, 2)
        self.assertEqual(self.events[-1]["event"], "owner_approved")

    def test_financial_tool_requires_owner_confirmation(self):
        approve = self.approver(confirm=True)
        self.assertTrue(approve("fund_milestone", {"amount": "10.00"}))
        self.assertEqual(self.confirmations[0].risk, MCPToolRisk.FINANCIAL)

    def test_denial_and_truthy_non_boolean_do_not_authorize(self):
        for result in (False, "yes", 1, None):
            with self.subTest(result=result):
                self.confirmations.clear()
                self.events.clear()
                approve = self.approver(confirm=result)
                self.assertFalse(approve("update_profile", {}))
                self.assertEqual(self.events[-1]["event"], "denied")

    def test_missing_scopes_deny_before_confirmation(self):
        approve = self.approver(scopes=("profile:read",))
        self.assertFalse(approve("update_profile", {}))
        self.assertFalse(self.events)
        self.assertFalse(self.confirmations)

    def test_audit_failure_denies_read_and_write(self):
        def broken(_event):
            raise RuntimeError("audit unavailable")
        approve = self.approver(audit=broken)
        self.assertFalse(approve("read_profile", {}))
        self.assertFalse(approve("update_profile", {}))
        self.assertFalse(self.confirmations)

    def test_audit_failure_after_owner_confirmation_still_denies(self):
        calls = []
        def audit(event):
            calls.append(event)
            if event["event"] == "owner_approved":
                raise RuntimeError("audit unavailable")
        approve = self.approver(audit=audit)
        self.assertFalse(approve("update_profile", {}))
        self.assertEqual([event["event"] for event in calls], ["requested", "owner_approved"])

    def test_approval_arguments_are_deeply_immutable(self):
        approve = self.approver()
        self.assertTrue(approve("update_profile", {"profile": {"name": "A"}}))
        with self.assertRaises(TypeError):
            self.confirmations[0].arguments["profile"]["name"] = "B"

    def test_invalid_policy_classification_and_duplicate_keys_rejected(self):
        with self.assertRaisesRegex(MCPToolPolicyError, "trusted_risk_class_required"):
            MCPToolPolicy("owner", "provider", "account", "tool", "read_only", (), 1)
        with self.assertRaisesRegex(MCPToolPolicyError, "duplicate_tool_policy"):
            MCPToolPolicyRegistry(
                owner_id="owner", policies=(self.read, self.read), revision=1,
            )

    def test_policy_owner_mismatch_and_registry_identity_fail_closed(self):
        foreign = MCPToolPolicy(
            "other-owner", "provider", "account", "read_profile",
            MCPToolRisk.READ_ONLY, (), 1,
        )
        with self.assertRaisesRegex(MCPToolPolicyError, "policy_owner_mismatch"):
            MCPToolPolicyRegistry(owner_id="owner", policies=(foreign,), revision=1)
        with self.assertRaisesRegex(MCPToolPolicyError, "authenticated_owner_mismatch"):
            self.registry.build_approver(
                owner_id="other-owner", provider_id="provider", account_id="account",
                granted_scopes=(), owner_approval=lambda _r: True, audit=lambda _e: None,
                current_revision=lambda: self.registry.revision,
            )

    def test_stale_or_unavailable_policy_revision_denies_every_call(self):
        stale = self.registry.build_approver(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",),
            owner_approval=lambda _request: True, audit=self.events.append,
            current_revision=lambda: self.registry.revision + 1,
        )
        self.assertFalse(stale("read_profile", {}))
        broken = self.registry.build_approver(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",),
            owner_approval=lambda _request: True, audit=self.events.append,
            current_revision=lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
        )
        self.assertFalse(broken("read_profile", {}))
        self.assertFalse(self.events)

    def test_disabled_policy_is_not_in_allowlist_or_authorized(self):
        disabled = MCPToolPolicy(
            "owner", "provider", "account", "disabled_tool",
            MCPToolRisk.READ_ONLY, (), 1, enabled=False,
        )
        registry = MCPToolPolicyRegistry(
            owner_id="owner", policies=(disabled,), revision=8,
        )
        self.assertEqual(registry.tool_names(provider_id="provider", account_id="account"), ())
        approve = registry.build_approver(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=(), owner_approval=lambda _request: True,
            audit=self.events.append, current_revision=lambda: 8,
        )
        self.assertFalse(approve("disabled_tool", {}))
        self.assertFalse(self.events)

    def test_boolean_is_not_accepted_as_policy_revision(self):
        approve = self.registry.build_approver(
            owner_id="owner", provider_id="provider", account_id="account",
            granted_scopes=("profile:read",),
            owner_approval=lambda _request: True, audit=self.events.append,
            current_revision=lambda: True,
        )
        self.assertFalse(approve("read_profile", {}))
        self.assertFalse(self.events)

    def test_invalid_arguments_and_non_finite_values_are_denied(self):
        approve = self.approver()
        self.assertFalse(approve("read_profile", {"bad": float("nan")}))
        self.assertFalse(approve("read_profile", []))


if __name__ == "__main__":
    unittest.main()
