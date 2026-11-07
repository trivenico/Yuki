import unittest

from core.reasoning.effort_policy import (
    ReasoningEffortError,
    available_effort_profiles,
    resolve_reasoning_budget,
)


class ReasoningEffortPolicyTests(unittest.TestCase):
    def test_task_classes_have_conservative_defaults(self):
        self.assertEqual(resolve_reasoning_budget(task_class="simple").profile, "low")
        self.assertEqual(resolve_reasoning_budget(task_class="conversation").profile, "standard")
        self.assertEqual(resolve_reasoning_budget(task_class="coding").profile, "high")
        self.assertEqual(resolve_reasoning_budget(task_class="safety_review").profile, "maximum")

    def test_explicit_effort_is_allow_listed(self):
        result = resolve_reasoning_budget(task_class="simple", requested_effort="high")
        self.assertEqual(result.profile, "high")
        with self.assertRaisesRegex(ReasoningEffortError, "unsupported_reasoning_effort"):
            resolve_reasoning_budget(task_class="simple", requested_effort="unlimited")

    def test_device_limits_can_only_reduce_policy_budget(self):
        base = resolve_reasoning_budget(task_class="engineering", requested_effort="high")
        reduced = resolve_reasoning_budget(
            task_class="engineering", requested_effort="high",
            device_limits={
                "max_model_turns": 2,
                "max_tool_calls": 3,
                "max_duration_seconds": 45,
                "max_transcript_chars": 8000,
            },
        )
        self.assertLessEqual(reduced.max_model_turns, base.max_model_turns)
        self.assertLessEqual(reduced.max_tool_calls, base.max_tool_calls)
        self.assertLessEqual(reduced.max_duration_seconds, base.max_duration_seconds)
        self.assertLessEqual(reduced.max_transcript_chars, base.max_transcript_chars)

    def test_device_limits_cannot_expand_budget(self):
        base = resolve_reasoning_budget(task_class="simple")
        expanded = resolve_reasoning_budget(
            task_class="simple", device_limits={"max_tool_calls": 10_000}
        )
        self.assertEqual(expanded.max_tool_calls, base.max_tool_calls)

    def test_unknown_task_classes_and_limits_are_rejected(self):
        with self.assertRaisesRegex(ReasoningEffortError, "unsupported_task_class"):
            resolve_reasoning_budget(task_class="arbitrary_shell")
        with self.assertRaisesRegex(ReasoningEffortError, "unsupported_device_limit"):
            resolve_reasoning_budget(
                task_class="coding", device_limits={"shell_command": 1}
            )

    def test_invalid_limit_types_are_rejected(self):
        with self.assertRaisesRegex(ReasoningEffortError, "invalid_device_limit"):
            resolve_reasoning_budget(task_class="coding", device_limits={"max_tool_calls": True})
        with self.assertRaisesRegex(ReasoningEffortError, "invalid_device_limit"):
            resolve_reasoning_budget(task_class="coding", device_limits={"max_duration_seconds": 0})

    def test_profiles_are_stable_and_bounded(self):
        profiles = available_effort_profiles()
        self.assertEqual(profiles, ("low", "standard", "high", "maximum"))
        for profile in profiles:
            result = resolve_reasoning_budget(task_class="conversation", requested_effort=profile)
            options = result.to_execution_options()
            self.assertTrue(all(type(value) is int and value > 0 for value in options.values()))
            self.assertLessEqual(options["max_duration_seconds"], 600)


if __name__ == "__main__":
    unittest.main()
