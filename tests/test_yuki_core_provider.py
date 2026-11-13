import unittest
from pathlib import Path

from providers.yuki_core import YukiCoreProvider


class FakeRuntime:
    def __init__(self, model_dir, *, device):
        self.model_dir = Path(model_dir)
        self.device = device
        self.prompts = []

    def is_available(self):
        return True

    def generate(self, prompt, *, max_new_tokens):
        self.prompts.append((prompt, max_new_tokens))
        return "A local research-model response."


class YukiCoreProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = YukiCoreProvider(
            "/models/yuki-core/0.1.0", device="cpu", runtime_factory=FakeRuntime,
        )

    def test_exposes_provider_readiness(self):
        self.assertTrue(self.provider.is_available())

    def test_generate_uses_bounded_runtime(self):
        self.assertEqual(self.provider.generate("hello"), "A local research-model response.")
        self.assertEqual(self.provider.runtime.prompts[-1][1], 256)

    def test_chat_preserves_role_boundaries_in_prompt(self):
        response = self.provider.generate_chat(
            messages=[
                {"role": "system", "content": "You are Yuki."},
                {"role": "user", "content": "Hello."},
            ],
            tools=[],
        )
        prompt = self.provider.runtime.prompts[-1][0]
        self.assertIn("[SYSTEM]\nYou are Yuki.", prompt)
        self.assertIn("[USER]\nHello.", prompt)
        self.assertEqual(response["tool_calls"], {"tool_calls": []})

    def test_rejects_tools_instead_of_claiming_agent_capability(self):
        tool = {"type": "function", "function": {"name": "open_file", "parameters": {}}}
        with self.assertRaisesRegex(ValueError, "does not support tools"):
            self.provider.generate_chat(
                messages=[{"role": "user", "content": "Open a file."}],
                tools=[tool],
            )

    def test_rejects_invalid_generation_inputs(self):
        with self.assertRaises(ValueError):
            self.provider.generate(" ")
        with self.assertRaises(ValueError):
            YukiCoreProvider("/models/yuki", device="remote", runtime_factory=FakeRuntime)


if __name__ == "__main__":
    unittest.main()
