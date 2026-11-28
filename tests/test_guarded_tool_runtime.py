"""Tests for exact-action guarded CLI tool dispatch."""
import unittest

from core.guarded_tool_runtime import GuardedToolRuntime
from core.tool_action import ToolActionEnvelope


LIVE_IDENTITY = lambda owner, session: owner == 'owner' and session.startswith('session')


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.actions = []

    def authorize_action(self, action):
        self.actions.append(action)
        return self.allowed


class GuardedToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.policy = Policy()
        self.runtime = GuardedToolRuntime(
            {"echo": lambda argument: self.calls.append(argument) or argument},
            self.policy,
            audit=lambda action, outcome: self.events.append((action, outcome)),
            max_argument_chars=10,
            max_result_chars=5,
            owner_id="owner",
            session_id="session-1",
            identity_validator=LIVE_IDENTITY,
        )

    def test_registered_tool_authorizes_exact_action_and_audits_metadata(self):
        self.assertEqual(self.runtime.invoke("echo", "hello"), "hello")
        self.assertEqual(self.calls, ["hello"])
        self.assertEqual(self.events[-1], ("tool:echo", "completed"))
        action = self.policy.actions[0]
        self.assertIsInstance(action, ToolActionEnvelope)
        self.assertEqual(action.tool_name, "echo")
        self.assertEqual(action.argument_bytes, 5)
        self.assertEqual(action.owner_id, "owner")
        self.assertEqual(action.session_id, "session-1")
        self.assertEqual(len(action.argument_sha256), 64)
        self.assertNotIn("hello", repr(action))

    def test_different_arguments_produce_different_authorization_context(self):
        self.runtime.invoke("echo", "hello")
        self.runtime.invoke("echo", "world")
        self.assertNotEqual(
            self.policy.actions[0].argument_sha256,
            self.policy.actions[1].argument_sha256,
        )

    def test_denied_tool_does_not_execute(self):
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: self.calls.append(argument) or argument},
            Policy(False),
            owner_id="owner", session_id="session-1", identity_validator=LIVE_IDENTITY,
        )
        self.assertIn("blocked", runtime.invoke("echo", "hello"))
        self.assertEqual(self.calls, [])

    def test_unknown_and_oversized_arguments_fail_closed(self):
        self.assertEqual(self.runtime.invoke("missing"), "Unknown tool.")
        self.assertIn("too long", self.runtime.invoke("echo", "x" * 11))
        self.assertEqual(self.calls, [])

    def test_result_is_bounded(self):
        self.assertEqual(self.runtime.invoke("echo", "123456789"), "12345… [truncated]")

    def test_tool_exception_is_reported_as_uncertain_without_leaking_details(self):
        runtime = GuardedToolRuntime(
            {"bad": lambda _: 1 / 0}, Policy(),
            owner_id="owner", session_id="session-1",
            identity_validator=LIVE_IDENTITY,
        )
        outcome = runtime.invoke_detailed("bad")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "outcome_unknown")
        self.assertIn("outcome is unknown", outcome.value)
        self.assertNotIn("division", outcome.value)

    def test_policy_exception_denies_execution(self):
        class BrokenPolicy:
            def authorize_action(self, action):
                raise RuntimeError("policy failure")
        runtime = GuardedToolRuntime({"echo": lambda _: self.calls.append("ran")}, BrokenPolicy(), owner_id="owner", session_id="session-1", identity_validator=LIVE_IDENTITY)
        self.assertIn("blocked", runtime.invoke("echo"))
        self.assertEqual(self.calls, [])

    def test_rejects_legacy_name_only_policy(self):
        class LegacyPolicy:
            def authorize_tool(self, name):
                return True
        with self.assertRaisesRegex(TypeError, "authorize_action"):
            GuardedToolRuntime({"echo": lambda _: "ok"}, LegacyPolicy())

    def test_rejects_non_dictionary_registry_and_non_callable_handlers(self):
        with self.assertRaises(TypeError):
            GuardedToolRuntime([], Policy())
        with self.assertRaises(TypeError):
            GuardedToolRuntime({"echo": "not callable"}, Policy())

    def test_rejects_invalid_names_and_policy_contract(self):
        for name in ("", "x" * 129, 123):
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                GuardedToolRuntime({name: lambda _: "ok"}, Policy())
        with self.assertRaises(TypeError):
            GuardedToolRuntime({"echo": lambda _: "ok"}, object())

    def test_rejects_invalid_limits_and_identity(self):
        for value in (0, -1, True, 1.5, "5", 1_000_001):
            with self.subTest(argument_limit=value), self.assertRaises(ValueError):
                GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), max_argument_chars=value)
            with self.subTest(result_limit=value), self.assertRaises(ValueError):
                GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), max_result_chars=value)
        for kwargs in ({"owner_id": ""}, {"session_id": "x" * 257}):
            with self.subTest(identity=kwargs), self.assertRaises(ValueError):
                GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), **kwargs)

    def test_nonempty_registry_requires_live_identity_validator(self):
        with self.assertRaisesRegex(ValueError, "live identity validation"):
            GuardedToolRuntime({"echo": lambda _: "ok"}, Policy())

    def test_session_revocation_between_authorization_and_dispatch_blocks_handler(self):
        validations = []
        def validator(owner, session):
            validations.append((owner, session))
            return len(validations) == 1
        runtime = GuardedToolRuntime(
            {"echo": lambda _: self.calls.append("ran") or "ok"},
            Policy(), owner_id="owner", session_id="session-1",
            identity_validator=validator,
        )
        self.assertIn("blocked", runtime.invoke("echo", "hello"))
        self.assertEqual(len(validations), 2)
        self.assertEqual(self.calls, [])

    def test_audit_failure_does_not_change_authorization_or_result(self):
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: argument},
            Policy(),
            owner_id="owner", session_id="session-1", identity_validator=LIVE_IDENTITY,
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit unavailable")),
        )
        self.assertEqual(runtime.invoke("echo", "ok"), "ok")


if __name__ == "__main__":
    unittest.main()
