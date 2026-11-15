"""Tests for delegated specialist permission intersection."""
import unittest

from core.delegated_tool_policy import DelegatedToolPolicy
from core.guarded_tool_runtime import GuardedToolRuntime


class MutableParentPolicy:
    def __init__(self, allowed):
        self.allowed = set(allowed)
        self.calls = []

    def authorize_tool(self, name):
        self.calls.append(name)
        return name in self.allowed


class DelegatedToolPolicyTests(unittest.TestCase):
    def test_specialist_allowlist_is_intersected_with_parent_policy(self):
        parent = MutableParentPolicy({"read", "write", "admin"})
        policy = DelegatedToolPolicy(parent, {"read", "write"})
        self.assertTrue(policy.authorize_tool("read"))
        self.assertTrue(policy.authorize_tool("write"))
        self.assertFalse(policy.authorize_tool("admin"))
        self.assertFalse(policy.authorize_tool("unknown"))
        self.assertEqual(parent.calls, ["read", "write"])

    def test_parent_revocation_takes_effect_without_rebuilding_specialist(self):
        parent = MutableParentPolicy({"read", "write"})
        policy = DelegatedToolPolicy(parent, {"read", "write"})
        self.assertTrue(policy.authorize_tool("write"))
        parent.allowed.remove("write")
        self.assertFalse(policy.authorize_tool("write"))

    def test_policy_exception_and_non_boolean_decision_fail_closed(self):
        class Broken:
            def authorize_tool(self, name):
                raise RuntimeError("policy unavailable")
        class Ambiguous:
            def authorize_tool(self, name):
                return 1
        self.assertFalse(DelegatedToolPolicy(Broken(), {"read"}).authorize_tool("read"))
        self.assertFalse(DelegatedToolPolicy(Ambiguous(), {"read"}).authorize_tool("read"))

    def test_guarded_runtime_cannot_execute_tool_outside_intersection(self):
        parent = MutableParentPolicy({"read", "delete"})
        calls = []
        policy = DelegatedToolPolicy(parent, {"read"})
        runtime = GuardedToolRuntime({
            "read": lambda _: calls.append("read") or "ok",
            "delete": lambda _: calls.append("delete") or "deleted",
        }, policy)
        self.assertEqual(runtime.invoke("read"), "ok")
        self.assertIn("blocked", runtime.invoke("delete"))
        self.assertEqual(calls, ["read"])

    def test_allowlist_is_immutable_and_validated(self):
        source = {"read"}
        policy = DelegatedToolPolicy(MutableParentPolicy(source), source)
        source.add("admin")
        self.assertEqual(policy.allowed_tools, frozenset({"read"}))
        with self.assertRaises(AttributeError):
            policy.allowed_tools.add("admin")
        for invalid in ({"bad name"}, {""}, {"x" * 129}, {1}, "read"):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                DelegatedToolPolicy(MutableParentPolicy(set()), invalid)

    def test_duplicate_allowlist_entries_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            DelegatedToolPolicy(MutableParentPolicy(set()), ["read", "read"])


if __name__ == "__main__":
    unittest.main()
