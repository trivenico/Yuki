"""Adversarial and contract tests for the central tool policy engine."""
import unittest

from core.tool_policy import (
    Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine, argument_digest,
)


def request(**changes):
    values = {
        "principal_id": "owner:local",
        "trigger": "interactive",
        "capability": "filesystem.write",
        "target": "workspace:notes",
        "argument_sha256": argument_digest({"path": "notes.txt", "content": "hello"}),
        "risk": Risk.MEDIUM,
        "run_id": "run-1",
        "step_id": "step-1",
    }
    values.update(changes)
    return PolicyRequest(**values)


class ToolPolicyTests(unittest.TestCase):
    def test_no_matching_rule_denies_by_default(self):
        result = ToolPolicyEngine([], policy_version="v1").evaluate(request())
        self.assertEqual(result.decision, Decision.DENY)
        self.assertEqual(result.reason, "no_matching_rule")

    def test_exact_allow_rule_allows(self):
        rule = PolicyRule("allow-notes", Decision.ALLOW,
                          principal_id="owner:local",
                          capability="filesystem.write",
                          target="workspace:notes")
        result = ToolPolicyEngine([rule], policy_version="v1").evaluate(request())
        self.assertEqual(result.decision, Decision.ALLOW)
        self.assertEqual(result.matched_rule_ids, ("allow-notes",))

    def test_deny_overrides_allow_independent_of_rule_order(self):
        allow = PolicyRule("allow", Decision.ALLOW, capability="filesystem.write")
        deny = PolicyRule("deny", Decision.DENY, target="workspace:notes")
        for rules in ([allow, deny], [deny, allow]):
            result = ToolPolicyEngine(rules, policy_version="v1").evaluate(request())
            self.assertEqual(result.decision, Decision.DENY)
            self.assertEqual(result.matched_rule_ids, ("deny",))

    def test_quarantine_overrides_deny_and_approval(self):
        rules = [
            PolicyRule("approval", Decision.REQUIRE_APPROVAL),
            PolicyRule("deny", Decision.DENY),
            PolicyRule("quarantine", Decision.QUARANTINE),
        ]
        result = ToolPolicyEngine(rules, policy_version="v1").evaluate(request())
        self.assertEqual(result.decision, Decision.QUARANTINE)
        self.assertEqual(result.matched_rule_ids, ("quarantine",))

    def test_approval_overrides_allow(self):
        rules = [
            PolicyRule("allow", Decision.ALLOW),
            PolicyRule("approval", Decision.REQUIRE_APPROVAL, risk=Risk.MEDIUM),
        ]
        self.assertEqual(
            ToolPolicyEngine(rules, policy_version="v1").evaluate(request()).decision,
            Decision.REQUIRE_APPROVAL,
        )

    def test_rule_scope_does_not_match_other_principal_or_target(self):
        rule = PolicyRule("owner-only", Decision.ALLOW, principal_id="owner:local",
                          target="workspace:notes")
        engine = ToolPolicyEngine([rule], policy_version="v1")
        self.assertEqual(engine.evaluate(request(principal_id="agent:claimed")).decision, Decision.DENY)
        self.assertEqual(engine.evaluate(request(target="workspace:secrets")).decision, Decision.DENY)

    def test_malformed_request_fails_closed(self):
        result = ToolPolicyEngine([], policy_version="v1").evaluate(object())
        self.assertEqual(result.decision, Decision.DENY)
        self.assertEqual(result.reason, "invalid_request")

    def test_request_rejects_bad_digest_and_untrusted_risk_string(self):
        with self.assertRaises(ValueError):
            request(argument_sha256="not-a-digest")
        with self.assertRaises(ValueError):
            request(risk="critical")

    def test_argument_digest_is_stable_and_order_independent(self):
        self.assertEqual(argument_digest({"a": 1, "b": 2}), argument_digest({"b": 2, "a": 1}))
        self.assertNotEqual(argument_digest({"a": 1}), argument_digest({"a": 2}))

    def test_argument_digest_rejects_non_json_and_nan(self):
        for value in (object(), {"value": float("nan")}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                argument_digest(value)

    def test_duplicate_rule_ids_and_invalid_rules_rejected(self):
        rule = PolicyRule("same", Decision.ALLOW)
        with self.assertRaises(ValueError):
            ToolPolicyEngine([rule, rule], policy_version="v1")
        with self.assertRaises(TypeError):
            ToolPolicyEngine([object()], policy_version="v1")

    def test_invalid_policy_version_rejected(self):
        with self.assertRaises(ValueError):
            ToolPolicyEngine([], policy_version="")

if __name__ == "__main__":
    unittest.main()
