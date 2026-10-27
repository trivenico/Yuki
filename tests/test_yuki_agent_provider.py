import unittest

from providers.factory import create_provider
from providers.openai_compatible import OpenAICompatibleProvider
from yuki_agent import YukiAgent


class _FakeProvider:
    def __init__(self, available=True, result="hello"):
        self.available = available
        self.result = result

    def is_available(self):
        return self.available

    def generate(self, prompt):
        return self.result


class YukiAgentProviderTests(unittest.TestCase):
    def test_default_agent_uses_configured_real_provider_not_mock(self):
        agent = YukiAgent(config={}, environ={})
        self.assertIsInstance(agent.provider, OpenAICompatibleProvider)
        self.assertEqual(agent.status(), "Provider is unavailable.")
        self.assertIn("no mock fallback", agent.ask("hello").lower())

    def test_explicit_provider_injection_is_supported(self):
        agent = YukiAgent(provider=_FakeProvider())
        self.assertEqual(agent.ask("hello"), "hello")

    def test_unavailable_injected_provider_does_not_generate(self):
        agent = YukiAgent(provider=_FakeProvider(available=False))
        self.assertIn("unavailable", agent.ask("hello").lower())

    def test_invalid_provider_is_rejected(self):
        with self.assertRaises(TypeError):
            YukiAgent(provider=object())

    def test_empty_prompt_is_rejected(self):
        agent = YukiAgent(provider=_FakeProvider())
        self.assertIn("enter a message", agent.ask(" "))


if __name__ == "__main__":
    unittest.main()
