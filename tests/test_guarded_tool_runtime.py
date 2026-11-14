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
        runtime = GuardedToolRuntime({"bad": lambda _: 1 / 0}, Policy())
        self.assertEqual(runtime.invoke("bad"), "The tool failed. Its external outcome may be uncertain.")

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

    def test_audit_failure_does_not_change_authorization_or_result(self):
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: argument},
            Policy(),
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit unavailable")),
        )
        self.assertEqual(runtime.invoke("echo", "ok"), "ok")



    def test_invoke_with_status_marks_handler_exception_uncertain(self):
        runtime = GuardedToolRuntime(
            {"send": lambda raw: (_ for _ in ()).throw(RuntimeError("ack lost"))},
            Policy(),
        )
        output, status = runtime.invoke_with_status("send", "{}")
        self.assertEqual(status, "uncertain")
        self.assertIn("outcome may be uncertain", output)


    def test_explicit_idempotency_capability_receives_stable_key(self):
        received = []
        def handler(argument, *, idempotency_key):
            received.append(idempotency_key)
            return "accepted"
        handler.supports_idempotency_key = True
        runtime = GuardedToolRuntime({"send": handler}, Policy())
        output, status = runtime.invoke_with_status("send", "{}", idempotency_key="f" * 64)
        self.assertEqual((output, status), ("accepted", "completed"))
        self.assertEqual(received, ["f" * 64])

    def test_handler_without_explicit_capability_does_not_receive_key(self):
        received = []
        def handler(argument):
            received.append(argument)
            return "accepted"
        runtime = GuardedToolRuntime({"send": handler}, Policy())
        self.assertEqual(runtime.invoke_with_status("send", "{}", idempotency_key="f" * 64),
                         ("accepted", "completed"))
        self.assertEqual(received, ["{}"])

    def test_invalid_explicit_idempotency_contract_fails_before_handler(self):
        calls = []
        def handler(argument):
            calls.append(argument)
            return "unsafe"
        handler.supports_idempotency_key = True
        runtime = GuardedToolRuntime({"send": handler}, Policy())
        self.assertEqual(runtime.invoke_with_status("send", "{}", idempotency_key="f" * 64)[1], "failed")
        self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()
