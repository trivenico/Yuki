"""Regression tests for Yuki's local tool and secret-redaction boundaries."""

import unittest

from yuki_security import SecurityPolicy, sanitize_text
from yuki_tools import calculate, run_tool


class SecurityRegressionTests(unittest.TestCase):
    def test_default_policy_allows_only_registered_low_risk_tools(self):
        policy = SecurityPolicy()
        self.assertTrue(policy.authorize_tool("calculate"))
        self.assertTrue(policy.authorize_tool("list_files"))
        self.assertFalse(policy.authorize_tool("shell"))
        self.assertFalse(policy.authorize_tool("python"))
        self.assertFalse(policy.authorize_tool("../yuki_security.py"))
        self.assertFalse(policy.authorize_tool(None))

    def test_policy_can_be_narrowed_to_explicit_subset(self):
        policy = SecurityPolicy(allowed_tools={"calculate"})
        self.assertTrue(policy.authorize_tool("calculate"))
        self.assertFalse(policy.authorize_tool("save_note"))
        self.assertFalse(policy.authorize_tool("shell"))

    def test_secret_redaction_covers_common_credential_shapes(self):
        sample = (
            "api_key=abc123 password: secret123 "
            "Bearer eyJhbGciOiJIUzI1NiJ9 "
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        )
        cleaned = sanitize_text(sample)
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("secret123", cleaned)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", cleaned)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_sanitize_text_bounds_output_and_handles_none(self):
        self.assertEqual(sanitize_text("abcdef", 3), "abc")
        self.assertEqual(sanitize_text(None), "")

    def test_calculator_rejects_code_execution_syntax(self):
        self.assertIn("Could not calculate", calculate("__import__('os').system('echo unsafe')"))
        self.assertIn("Could not calculate", calculate("(1).__class__"))

    def test_calculator_bounds_expression_and_exponent(self):
        self.assertEqual(calculate("12 * (3 + 4)"), "84")
        self.assertIn("too long", calculate("1" * 201))
        self.assertIn("Exponent is too large", calculate("2 ** 9"))

    def test_unknown_tool_never_dispatches_arbitrary_callable(self):
        result = run_tool("shell", "echo unsafe")
        self.assertIn("Unknown tool", result)


if __name__ == "__main__":
    unittest.main()
