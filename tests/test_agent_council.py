import unittest
from threading import Barrier, Lock

from core.agent_council import AgentCouncil


class ParallelFakeProvider:
    def __init__(self):
        self.barrier = Barrier(3)
        self.lock = Lock()
        self.prompts = []
        self.specialist_count = 0

    def generate(self, prompt):
        with self.lock:
            self.prompts.append(prompt)
        if "Specialist role:" in prompt:
            self.barrier.wait(timeout=2)
            with self.lock:
                self.specialist_count += 1
            role_line = next(line for line in prompt.splitlines() if line.startswith("Specialist role:"))
            return f"Independent recommendation from {role_line}."
        return "Consensus: use a staged, tested, reversible plan; verify each milestone."

    def is_available(self):
        return True


class AgentCouncilTests(unittest.TestCase):
    def test_runs_three_specialists_in_parallel_then_synthesizes(self):
        provider = ParallelFakeProvider()
        events = []
        result = AgentCouncil(provider, timeout_seconds=5, on_event=events.append).run("Improve project reliability")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.contributions), 3)
        self.assertEqual(provider.specialist_count, 3)
        self.assertEqual(len(provider.prompts), 7)
        self.assertEqual(sum("Peer-review role:" in prompt for prompt in provider.prompts), 3)
        self.assertIn("Consensus", result.summary)
        self.assertTrue(any("comparing proposals" in event for event in events))
        self.assertIn("Independent recommendation", result.context)
        self.assertIn("Peer critique:", result.context)

    def test_partial_specialist_failure_still_debates_available_proposals(self):
        class PartlyBrokenProvider(ParallelFakeProvider):
            def generate(self, prompt):
                answer = super().generate(prompt)
                if "Specialist role:" in prompt and "(skeptic)" in prompt:
                    raise RuntimeError("critic temporarily unavailable")
                return answer

        provider = PartlyBrokenProvider()
        result = AgentCouncil(provider, timeout_seconds=5).run("Improve project reliability")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.contributions), 2)
        self.assertIn("Peer critique:", result.context)

    def test_empty_goal_does_not_call_provider(self):
        provider = ParallelFakeProvider()
        result = AgentCouncil(provider).run("  ")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "invalid_input")
        self.assertEqual(provider.prompts, [])

    def test_provider_failure_fails_safely_without_claiming_actions(self):
        class BrokenProvider:
            def generate(self, prompt):
                raise RuntimeError("provider unavailable")
        result = AgentCouncil(BrokenProvider()).run("Change the code")
        self.assertFalse(result.ok)
        self.assertIn("No actions were taken", result.summary)

    def test_invalid_worker_and_timeout_bounds_are_rejected(self):
        provider = ParallelFakeProvider()
        with self.assertRaises(ValueError):
            AgentCouncil(provider, max_workers=4)
        with self.assertRaises(ValueError):
            AgentCouncil(provider, timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
