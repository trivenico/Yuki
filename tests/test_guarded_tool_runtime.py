"""Tests for guarded CLI tool dispatch."""
import unittest

from core.guarded_tool_runtime import GuardedToolRuntime


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def authorize_tool(self, name):
        return self.allowed


class GuardedToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.events = []
        self.runtime = GuardedToolRuntime(
            {"echo": lambda argument: self.calls.append(argument) or argument},
            Policy(),
            audit=lambda action, outcome: self.events.append((action, outcome)),
            max_argument_chars=10,
            max_result_chars=5,
        )

    def test_registered_tool_runs_and_audits_metadata(self):
        self.assertEqual(self.runtime.invoke("echo", "hello"), "hello")
        self.assertEqual(self.calls, ["hello"])
        self.assertEqual(self.events[-1], ("tool:echo", "completed"))

    def test_denied_tool_does_not_execute(self):
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: self.calls.append(argument) or argument},
            Policy(False),
        )
        self.assertIn("blocked", runtime.invoke("echo", "hello"))
        self.assertEqual(self.calls, [])

    def test_unknown_and_oversized_arguments_fail_closed(self):
        self.assertEqual(self.runtime.invoke("missing"), "Unknown tool.")
        self.assertIn("too long", self.runtime.invoke("echo", "x" * 11))
        self.assertEqual(self.calls, [])

    def test_result_is_bounded(self):
        self.assertEqual(self.runtime.invoke("echo", "123456789"), "12345… [truncated]")

    def test_tool_exception_does_not_leak_exception(self):
        runtime = GuardedToolRuntime(
            {"bad": lambda _: 1 / 0}, Policy(), audit=lambda _action, _outcome: True
        )
        self.assertEqual(runtime.invoke("bad"), "The tool failed. Check the input and try again.")

    def test_missing_audit_sink_blocks_execution(self):
        calls = []
        runtime = GuardedToolRuntime({"echo": lambda arg: calls.append(arg) or arg}, Policy())
        self.assertIn("blocked", runtime.invoke("echo", "ok").lower())
        self.assertEqual(calls, [])

    def test_policy_exception_denies_execution(self):
        class BrokenPolicy:
            def authorize_tool(self, name):
                raise RuntimeError("policy failure")
        runtime = GuardedToolRuntime({"echo": lambda _: self.calls.append("ran")}, BrokenPolicy())
        self.assertIn("blocked", runtime.invoke("echo"))
        self.assertEqual(self.calls, [])

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

    def test_rejects_invalid_limits(self):
        for value in (0, -1, True, 1.5, "5", 1_000_001):
            with self.subTest(argument_limit=value), self.assertRaises(ValueError):
                GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), max_argument_chars=value)
            with self.subTest(result_limit=value), self.assertRaises(ValueError):
                GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), max_result_chars=value)

    def test_audit_failure_blocks_tool_before_execution(self):
        calls = []
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: calls.append(argument) or argument},
            Policy(),
            audit=lambda *_: False,
        )
        self.assertIn("blocked", runtime.invoke("echo", "ok").lower())
        self.assertEqual(calls, [])

    def test_completion_audit_failure_warns_that_tool_may_have_run(self):
        calls = []
        def audit(_action, outcome):
            return outcome != "completed"
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: calls.append(argument) or argument},
            Policy(),
            audit=audit,
        )
        result = runtime.invoke("echo", "ok")
        self.assertIn("audit logging failed", result.lower())
        self.assertEqual(calls, ["ok"])

    def test_audit_events_bracket_execution_without_arguments(self):
        events = []
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: argument},
            Policy(),
            audit=lambda action, outcome: events.append((action, outcome)),
        )
        self.assertEqual(runtime.invoke("echo", "private input"), "private input")
        self.assertEqual(events, [("tool:echo", "started"), ("tool:echo", "completed")])
        self.assertNotIn("private input", repr(events))


if __name__ == "__main__":
    unittest.main()
