import unittest

from core.sandbox_policy import (
    SandboxPolicy,
    SandboxPolicyError,
    default_sandbox_policy,
)


class SandboxPolicyTests(unittest.TestCase):
    def test_default_is_restrictive_and_valid(self):
        policy = default_sandbox_policy()
        self.assertEqual(policy.network_mode, "none")
        self.assertEqual(policy.capabilities, frozenset())
        self.assertTrue(policy.read_only_root)
        self.assertTrue(policy.run_as_non_root)

    def test_network_requires_explicit_allowlist_capability(self):
        with self.assertRaises(SandboxPolicyError):
            SandboxPolicy(network_mode="allowlist").validate()

    def test_network_capability_conflicts_with_disabled_network(self):
        with self.assertRaises(SandboxPolicyError):
            SandboxPolicy(capabilities=frozenset({"network_allowlist"})).validate()

    def test_unknown_capability_is_rejected(self):
        with self.assertRaises(SandboxPolicyError):
            SandboxPolicy(capabilities=frozenset({"host_root"})).validate()

    def test_boolean_and_non_integer_limits_are_rejected(self):
        for value in (True, 1.5, "30", None):
            with self.subTest(value=value):
                with self.assertRaises(SandboxPolicyError):
                    SandboxPolicy(timeout_seconds=value).validate()

    def test_out_of_range_limits_are_rejected(self):
        for value in (0, 3601):
            with self.subTest(value=value):
                with self.assertRaises(SandboxPolicyError):
                    SandboxPolicy(timeout_seconds=value).validate()

    def test_writable_root_or_root_user_is_rejected(self):
        with self.assertRaises(SandboxPolicyError):
            SandboxPolicy(read_only_root=False).validate()
        with self.assertRaises(SandboxPolicyError):
            SandboxPolicy(run_as_non_root=False).validate()

    def test_explicit_allowlisted_network_policy_is_valid(self):
        policy = SandboxPolicy(
            network_mode="allowlist",
            capabilities=frozenset({"network_allowlist"}),
        )
        self.assertIs(policy.validate(), policy)


if __name__ == "__main__":
    unittest.main()
