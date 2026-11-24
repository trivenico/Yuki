"""Tests for the typed control-plane authorization contract."""
import unittest

from core.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    Principal,
    evaluate_authorization,
    require_authorized,
)


class AllowPolicy:
    def authorize(self, request):
        return AuthorizationDecision(True, "decision-1", "policy-v1", "allowed")


class AuthorizationContractTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal(
            "user-1", "human", tenant_id="tenant-1",
            permissions=frozenset({"tool:read"}),
        )
        self.request = AuthorizationRequest(
            self.principal, "tool:invoke", "document:123",
            risk="high", task_id="task-1",
            expected_policy_revision="policy-v1",
        )

    def test_typed_allow_is_returned(self):
        decision = require_authorized(AllowPolicy(), self.request)
        self.assertIs(decision.allowed, True)
        self.assertEqual(decision.policy_revision, "policy-v1")

    def test_denial_raises_permission_error(self):
        class DenyPolicy:
            def authorize(self, request):
                return AuthorizationDecision(False, "d-2", "policy-v1", "scope_denied")
        with self.assertRaises(PermissionError):
            require_authorized(DenyPolicy(), self.request)

    def test_policy_exception_fails_closed(self):
        class BrokenPolicy:
            def authorize(self, request):
                raise RuntimeError("internal policy detail")
        decision = evaluate_authorization(BrokenPolicy(), self.request)
        self.assertIs(decision.allowed, False)
        self.assertEqual(decision.reason_code, "policy_unavailable")
        self.assertNotIn("internal policy detail", decision.reason_code)

    def test_malformed_policy_result_fails_closed(self):
        class MalformedPolicy:
            def authorize(self, request):
                return {"allowed": True}
        self.assertIs(evaluate_authorization(MalformedPolicy(), self.request).allowed, False)

    def test_stale_policy_revision_fails_closed(self):
        class StalePolicy:
            def authorize(self, request):
                return AuthorizationDecision(True, "d-3", "policy-v0", "allowed")
        decision = evaluate_authorization(StalePolicy(), self.request)
        self.assertIs(decision.allowed, False)
        self.assertEqual(decision.reason_code, "stale_policy")

    def test_boolean_subclasses_and_truthy_values_are_rejected(self):
        for value in (1, "yes", object(), None):
            with self.subTest(value=type(value).__name__), self.assertRaises(TypeError):
                AuthorizationDecision(value, "d", "v1", "allowed")

    def test_request_rejects_untrusted_principal_shapes(self):
        with self.assertRaises(TypeError):
            AuthorizationRequest({"principal_id": "user-1"}, "read", "file:1")
        with self.assertRaises(ValueError):
            Principal(" user-1 ", "human")

    def test_invalid_risk_and_blank_scope_are_rejected(self):
        with self.assertRaises(ValueError):
            AuthorizationRequest(self.principal, "read", "file:1", risk="urgent")
        with self.assertRaises(ValueError):
            AuthorizationRequest(self.principal, "read", " ")

    def test_missing_or_invalid_policy_fails_closed(self):
        decision = evaluate_authorization(object(), self.request)
        self.assertIs(decision.allowed, False)
        with self.assertRaises(PermissionError):
            require_authorized(object(), self.request)


if __name__ == "__main__":
    unittest.main()
