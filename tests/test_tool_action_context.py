import json
import unittest

from core.guarded_tool_runtime import GuardedToolRuntime
from core.tool_action_context import ToolActionContext, ToolActionContextError
from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog, ToolEffect


class Policy:
    def authorize_tool(self, name):
        return True


class ToolActionContextTests(unittest.TestCase):
    def setUp(self):
        self.request = {"to": "person@example.com", "subject": "Hello"}
        self.context = ToolActionContext.create("user-123", "email.send", self.request)

    def test_context_binds_principal_tool_and_canonical_request(self):
        self.context.verify("user-123", "email.send", {"subject": "Hello", "to": "person@example.com"})
        with self.assertRaises(ToolActionContextError):
            self.context.verify("user-456", "email.send", self.request)
        with self.assertRaises(ToolActionContextError):
            self.context.verify("user-123", "calendar.create", self.request)
        with self.assertRaises(ToolActionContextError):
            self.context.verify("user-123", "email.send", {"to": "other@example.com"})

    def test_distinct_intentional_actions_get_distinct_host_ids(self):
        other = ToolActionContext.create("user-123", "email.send", self.request)
        self.assertNotEqual(self.context.action_id, other.action_id)
        self.assertNotEqual(self.context.idempotency_key, other.idempotency_key)

    def test_invalid_principal_tool_and_non_json_requests_are_rejected(self):
        for principal in ("", "bad\nprincipal", None):
            with self.subTest(principal=principal), self.assertRaises(ToolActionContextError):
                ToolActionContext.create(principal, "email.send", self.request)
        with self.assertRaises(ToolActionContextError):
            ToolActionContext.create("user-123", "../send", self.request)
        with self.assertRaises(ToolActionContextError):
            ToolActionContext.create("user-123", "email.send", {"bad": float("nan")})

    def test_context_is_immutable(self):
        with self.assertRaises((AttributeError, TypeError)):
            self.context.principal_id = "attacker"

    def test_strict_runtime_requires_matching_context_for_side_effects(self):
        calls = []
        runtime = GuardedToolRuntime(
            {"email.send": lambda argument: calls.append(argument) or "sent"},
            Policy(),
            capabilities=ToolCapabilityCatalog({
                "email.send": ToolCapability(ToolEffect.SIDE_EFFECTING),
            }),
            require_action_context=True,
        )
        argument = json.dumps(self.request, separators=(",", ":"))
        self.assertIn("required", runtime.invoke("email.send", argument))
        self.assertIn(
            "does not match",
            runtime.invoke(
                "email.send", argument,
                action_context=self.context, principal_id="another-user",
            ),
        )
        self.assertEqual(
            runtime.invoke(
                "email.send", argument,
                action_context=self.context, principal_id="user-123",
            ),
            "sent",
        )
        self.assertEqual(len(calls), 1)

    def test_read_only_tools_do_not_require_action_context(self):
        runtime = GuardedToolRuntime(
            {"email.list": lambda _: "[]"},
            Policy(),
            capabilities=ToolCapabilityCatalog({
                "email.list": ToolCapability(ToolEffect.READ_ONLY),
            }),
            require_action_context=True,
        )
        self.assertEqual(runtime.invoke("email.list", "{}"), "[]")

    def test_strict_mode_requires_capability_catalog(self):
        with self.assertRaises(ValueError):
            GuardedToolRuntime({"email.send": lambda _: "sent"}, Policy(), require_action_context=True)


if __name__ == "__main__":
    unittest.main()
