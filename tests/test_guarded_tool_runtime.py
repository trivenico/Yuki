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
        self.assertEqual(runtime.invoke("bad"), "The tool failed. Check the input and try again.")

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


    def test_central_policy_must_allow_before_legacy_policy_and_execution(self):
        from core.tool_policy import (
            Decision, PolicyRequest, PolicyResult, Risk, argument_digest,
        )

        calls = []
        class Engine:
            def __init__(self, decision):
                self.decision = decision
                self.requests = []
            def evaluate(self, request):
                self.requests.append(request)
                return PolicyResult(self.decision, "v1", ("rule",), "test")

        def request_factory(name, argument):
            return PolicyRequest(
                principal_id="owner:local",
                trigger="interactive",
                capability="tool:" + name,
                target="local:tool",
                argument_sha256=argument_digest(argument),
                risk=Risk.LOW,
                run_id="run-1",
                step_id="step-1",
            )

        engine = Engine(Decision.DENY)
        runtime = GuardedToolRuntime(
            {"echo": lambda value: calls.append(value) or value},
            Policy(True),
            policy_engine=engine,
            policy_request_factory=request_factory,
        )
        self.assertIn("central security policy", runtime.invoke("echo", "hello"))
        self.assertEqual(calls, [])
        self.assertEqual(engine.requests[0].argument_sha256, argument_digest("hello"))

    def test_central_policy_approval_and_quarantine_do_not_execute(self):
        from core.tool_policy import Decision, PolicyResult
        class Engine:
            def __init__(self, decision):
                self.decision = decision
            def evaluate(self, request):
                return PolicyResult(self.decision, "v1", ("rule",), "test")
        for decision in (Decision.REQUIRE_APPROVAL, Decision.QUARANTINE):
            calls = []
            runtime = GuardedToolRuntime(
                {"echo": lambda value: calls.append(value) or value},
                Policy(True),
                policy_engine=Engine(decision),
                policy_request_factory=lambda name, argument: object(),
            )
            self.assertIn("central security policy", runtime.invoke("echo", "hello"))
            self.assertEqual(calls, [])

    def test_central_policy_factory_or_engine_failure_denies(self):
        class BrokenEngine:
            def evaluate(self, request):
                raise RuntimeError("policy unavailable")
        calls = []
        runtime = GuardedToolRuntime(
            {"echo": lambda value: calls.append(value) or value},
            Policy(True),
            policy_engine=BrokenEngine(),
            policy_request_factory=lambda name, argument: object(),
        )
        self.assertIn("evaluation failed", runtime.invoke("echo", "hello"))
        self.assertEqual(calls, [])

    def test_central_policy_requires_engine_and_factory_together(self):
        from core.tool_policy import ToolPolicyEngine
        engine = ToolPolicyEngine([], policy_version="v1")
        with self.assertRaises(ValueError):
            GuardedToolRuntime({"echo": lambda _: "ok"}, Policy(), policy_engine=engine)

    def test_audit_failure_does_not_change_authorization_or_result(self):
        runtime = GuardedToolRuntime(
            {"echo": lambda argument: argument},
            Policy(),
            audit=lambda *_: (_ for _ in ()).throw(OSError("audit unavailable")),
        )
        self.assertEqual(runtime.invoke("echo", "ok"), "ok")


if __name__ == "__main__":
    unittest.main()
