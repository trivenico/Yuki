"""Tests for Yuki's provider-independent native agent mode."""
import unittest

from yuki_agent import YukiAgent
from core.agent import YukiCoreAgent
from core.permissions import PermissionGate


class FakeProvider:
    def __init__(self, available=True, response="model response"):
        self.available = available
        self.response = response
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.response


class YukiAgentNativeModeTests(unittest.TestCase):
    def test_default_agent_does_not_substitute_a_mock_provider(self):
        agent = YukiAgent()
        self.assertIsNone(agent.provider)
        self.assertIn("native core is ready", agent.status().lower())

    def test_native_mode_explains_model_dependent_chat_without_fabricating_answer(self):
        agent = YukiAgent()
        response = agent.ask("Explain this request")
        self.assertIn("native mode is active", response.lower())
        self.assertIn("free-form generation", response.lower())

    def test_native_planner_decomposes_goal_without_a_model_provider(self):
        agent = YukiAgent()
        response = agent.ask(
            "Make a plan to research and build a secure app, then test and report results"
        )
        self.assertIn("Native plan proposal", response)
        self.assertIn("[research]", response)
        self.assertIn("[implementation]", response)
        self.assertIn("depends on: research", response)
        self.assertIn("nothing has been executed", response)

    def test_native_planner_uses_bounded_local_memory_context(self):
        agent = YukiAgent(memory_search=lambda query: ["Saved lesson: verify every change"])
        response = agent.ask("Make a plan to research this project")
        self.assertIn("Saved lesson: verify every change", response)
        self.assertIn("reference only", response)

    def test_native_plan_precedes_optional_provider(self):
        provider = FakeProvider()
        agent = YukiAgent(provider=provider)
        response = agent.ask("Create a roadmap to research and test this project")
        self.assertIn("Native plan proposal", response)
        self.assertEqual(provider.prompts, [])

    def test_native_registered_capability_runs_without_a_model_provider(self):
        native = YukiCoreAgent(
            permission_gate=PermissionGate(approver=lambda request: True)
        )
        agent = YukiAgent(native_agent=native)
        response = agent.ask("what time is it")
        self.assertTrue(response.startswith("Yuki: "))
        self.assertIn(" at ", response)
        self.assertIsNone(agent.provider)

    def test_native_capability_takes_precedence_over_optional_provider(self):
        native = YukiCoreAgent(
            permission_gate=PermissionGate(approver=lambda request: True)
        )
        provider = FakeProvider()
        agent = YukiAgent(provider=provider, native_agent=native)
        response = agent.ask("what time is it")
        self.assertTrue(response.startswith("Yuki: "))
        self.assertEqual(provider.prompts, [])

    def test_optional_provider_cannot_bypass_native_approval_denial(self):
        native = YukiCoreAgent(permission_gate=PermissionGate())
        provider = FakeProvider()
        agent = YukiAgent(provider=provider, native_agent=native)
        response = agent.ask("what time is it")
        self.assertIn("approval was not granted", response)
        self.assertEqual(provider.prompts, [])

    def test_provider_can_be_enabled_and_disabled_explicitly(self):
        agent = YukiAgent()
        provider = FakeProvider()
        agent.set_provider(provider)
        self.assertEqual(agent.ask("hello"), "model response")
        self.assertEqual(provider.prompts, ["hello"])
        agent.set_provider(None)
        self.assertIsNone(agent.provider)
        self.assertIn("native mode is active", agent.ask("hello").lower())

    def test_provider_errors_remain_sanitized(self):
        class FailingProvider(FakeProvider):
            def generate(self, prompt):
                raise RuntimeError("sensitive provider payload")

        agent = YukiAgent(FailingProvider())
        self.assertIn("optional provider encountered an error", agent.ask("hello"))

    def test_oversized_prompt_is_rejected_before_native_or_provider_work(self):
        provider = FakeProvider()
        agent = YukiAgent(provider=provider)
        response = agent.ask("x" * 20_001)
        self.assertIn("under 20000 characters", response)
        self.assertEqual(provider.prompts, [])

    def test_empty_prompt_is_rejected_in_native_mode(self):
        self.assertEqual(YukiAgent().ask(" "), "Yuki: Please enter a message.")


if __name__ == "__main__":
    unittest.main()
