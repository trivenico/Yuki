import tempfile
import unittest
from pathlib import Path

from core.onboarding import validate_config, save_config, load_user_config
from providers.factory import create_provider
from providers.openai_compatible import OpenAICompatibleProvider
from skills.proposals import SkillProposalStore


class NativeInferenceTests(unittest.TestCase):
    def test_native_provider_requires_existing_user_model(self):
        with self.assertRaises(ValueError):
            create_provider({"provider":"llama.cpp-python","model_path":"/no/such/model.gguf"}, environ={})

    def test_vllm_uses_local_openai_compatible_endpoint(self):
        provider = create_provider({"provider":"vllm","model":"test-model"}, environ={})
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertTrue(provider.is_available())

    def test_onboarding_persists_native_model_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config({"provider":"mlx","model":"local-model","model_path":"mlx-community/model",
                         "max_tokens":1024}, path)
            config = load_user_config(path)
            self.assertEqual(config["model_path"], "mlx-community/model")
            self.assertEqual(config["max_tokens"], 1024)

    def test_native_settings_are_range_checked(self):
        with self.assertRaises(ValueError):
            validate_config({"provider":"llama.cpp-python","n_ctx":10})


class SkillSandboxPreviewTests(unittest.TestCase):
    def test_provider_draft_is_saved_only_as_pending_proposal(self):
        draft = "---\nname: drafted-skill\ndescription: Drafted safely\nversion: 1.0.0\n---\n\nSummarize only user-provided text. Do not call tools.\n"
        class FakeProvider:
            def generate(self, prompt):
                self.prompt = prompt
                return draft
        with tempfile.TemporaryDirectory() as directory:
            store = SkillProposalStore(directory)
            provider = FakeProvider()
            proposal = store.draft(provider, "summarize user-provided notes")
            self.assertEqual(proposal.name, "drafted-skill")
            self.assertIn("declarative SKILL.md", provider.prompt)
            self.assertEqual(store.list_proposals()[0]["status"], "pending")
            self.assertFalse((Path(directory) / "approved" / "drafted-skill.SKILL.md").exists())

    def test_preview_is_static_and_never_executes(self):
        content = "---\nname: demo-skill\ndescription: Demonstration\n---\n\nSummarize text the user provides.\n"
        with tempfile.TemporaryDirectory() as directory:
            store = SkillProposalStore(directory)
            store.propose(content)
            preview = store.sandbox_preview("demo-skill")
            self.assertFalse(preview["checks"]["executed"])
            self.assertEqual(preview["status"], "static-review-only")


if __name__ == "__main__":
    unittest.main()
