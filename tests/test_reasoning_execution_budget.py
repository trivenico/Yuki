import unittest

from core.reasoning.execution_budget import build_budgeted_execution_loop
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class AllowPolicy:
    def authorize_tool(self, name):
        return name == "echo"


class ExecutionBudgetAdapterTests(unittest.TestCase):
    def setUp(self):
        runtime = GuardedToolRuntime({"echo": lambda raw: raw}, AllowPolicy())
        self.protocol = StructuredToolProtocol(runtime)
        self.model = lambda **kwargs: {
            "text": "ok", "tool_calls": {"tool_calls": []}
        }

    def test_policy_budget_is_applied_to_actual_execution_loop(self):
        budget = {
            "profile": "low",
            "max_model_turns": 2,
            "max_tool_calls": 1,
            "max_duration_seconds": 15,
            "max_transcript_chars": 5000,
            "rationale": "bounded local task",
        }
        loop = build_budgeted_execution_loop(self.model, self.protocol, budget)
        self.assertEqual(loop.max_model_turns, 2)
        self.assertEqual(loop.max_tool_calls, 1)
        self.assertEqual(loop.max_duration_seconds, 15)
        self.assertEqual(loop.max_transcript_chars, 5000)

    def test_policy_cannot_raise_existing_execution_hard_defaults(self):
        budget = {
            "max_model_turns": 100,
            "max_tool_calls": 500,
            "max_duration_seconds": 3600,
            "max_transcript_chars": 1_000_000,
        }
        loop = build_budgeted_execution_loop(self.model, self.protocol, budget)
        self.assertEqual(loop.max_model_turns, 8)
        self.assertEqual(loop.max_tool_calls, 20)
        self.assertEqual(loop.max_duration_seconds, 120)
        self.assertEqual(loop.max_transcript_chars, 80_000)

    def test_runtime_can_only_reduce_policy_budget(self):
        budget = {
            "max_model_turns": 6,
            "max_tool_calls": 12,
            "max_duration_seconds": 90,
            "max_transcript_chars": 40_000,
        }
        loop = build_budgeted_execution_loop(
            self.model, self.protocol, budget, max_model_turns=3,
            max_tool_calls=4,
        )
        self.assertEqual(loop.max_model_turns, 3)
        self.assertEqual(loop.max_tool_calls, 4)

    def test_invalid_or_unknown_budget_fields_are_rejected(self):
        base = {
            "max_model_turns": 2,
            "max_tool_calls": 2,
            "max_duration_seconds": 30,
            "max_transcript_chars": 1000,
        }
        for budget in (
            {**base, "max_model_turns": True},
            {**base, "max_tool_calls": 0},
            {**base, "arbitrary_option": 1},
            {key: value for key, value in base.items() if key != "max_duration_seconds"},
        ):
            with self.subTest(budget=budget), self.assertRaises((TypeError, ValueError)):
                build_budgeted_execution_loop(self.model, self.protocol, budget)


if __name__ == "__main__":
    unittest.main()
