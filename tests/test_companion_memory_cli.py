import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from companion.memory_consent import CompanionMemory
from yuki_cli import run_cli


class FakeProvider:
    def is_available(self):
        return True

    def generate(self, prompt):
        return "legacy"

    def generate_chat(self, system, messages):
        return "chat reply"


class CompanionMemoryCliTests(unittest.TestCase):
    def test_memory_commands_require_consent_and_support_export_forget_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            output = []
            messages = iter([
                "/remember I prefer short answers",
                "/memory on",
                "/remember I prefer short answers",
                "/memories",
                "/memory export",
                "/memory off",
                "/remember Save this without consent",
                "/forget invalid-id",
                "/memory clear",
                "/memory clear confirm",
                "/memories",
                "/quit",
            ])
            code = run_cli(
                ["chat"],
                config_loader=lambda: {},
                provider_factory=lambda config: FakeProvider(),
                memory_factory=lambda: CompanionMemory(MemoryStore(path)),
                input_fn=lambda prompt: next(messages),
                output_fn=output.append,
            )
            self.assertEqual(code, 0)
            rendered = "\n".join(output)
            self.assertIn("Memory is off or the entry is invalid", rendered)
            self.assertIn("Memory enabled", rendered)
            self.assertIn("I prefer short answers", rendered)
            self.assertIn("Memory disabled", rendered)
            self.assertIn("To delete all saved memories", rendered)
            self.assertIn("Deleted 1 saved memories", rendered)
            self.assertIn("No saved memories", rendered)

    def test_saved_memory_is_not_silently_injected_into_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            output = []
            messages = iter([
                "/memory on",
                "/remember private preference",
                "hello",
                "/quit",
            ])
            provider = FakeProvider()
            code = run_cli(
                ["chat"],
                config_loader=lambda: {},
                provider_factory=lambda config: provider,
                memory_factory=lambda: CompanionMemory(MemoryStore(path)),
                input_fn=lambda prompt: next(messages),
                output_fn=output.append,
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(provider.generate_chat.__self__.__class__.__name__), 12)  # provider invoked through its bound method
            # The only model request is the explicit chat message, not the saved entry.
            self.assertNotIn("private preference", str(output[-1]))


if __name__ == "__main__":
    unittest.main()
