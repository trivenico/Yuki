"""Tests for Yuki's provider-independent native agent mode."""
import unittest

from yuki_agent import YukiAgent


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
        self.assertEqual(agent.ask("hello"), "Yuki: The provider encountered an error.")

    def test_empty_prompt_is_rejected_in_native_mode(self):
        self.assertEqual(YukiAgent().ask(" "), "Yuki: Please enter a message.")


if __name__ == "__main__":
    unittest.main()
