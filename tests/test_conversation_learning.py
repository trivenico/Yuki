import json
import tempfile
import unittest
from pathlib import Path

from learning.conversation_import import ConversationImportStore
from learning.local_learning import LocalLearningPipeline


class FakeOllama:
    def __init__(self):
        self.prompts = []
    def generate(self, prompt):
        self.prompts.append(prompt)
        return '{"task_patterns":["debugging"],"useful_lessons":["run tests"],"failure_modes":[],"candidate_examples":[]}'


class ConversationImportTests(unittest.TestCase):
    def test_preview_chatgpt_mapping_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "export.json"
            source.write_text(json.dumps({"mapping": {"a": {"message": {
                "id": "m1", "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["Hello"]},
            }}}}), encoding="utf-8")
            result = ConversationImportStore(Path(tmp) / "store.json").preview(source)
            self.assertEqual(result["message_count"], 1)
            self.assertEqual(result["messages"][0]["text"], "Hello")

    def test_preview_chatgpt_root_conversations_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "conversations.json"
            source.write_text(json.dumps([{
                "title": "Imported ChatGPT chat",
                "mapping": {
                    "node-1": {"message": {
                        "id": "m1", "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": ["Hello from ChatGPT"]},
                    }},
                    "node-2": {"message": {
                        "id": "m2", "author": {"role": "assistant"},
                        "content": {"content_type": "text", "parts": ["Hello back"]},
                    }},
                },
            }]), encoding="utf-8")
            result = ConversationImportStore(Path(tmp) / "store.json").preview(source)
            self.assertEqual(result["message_count"], 2)
            self.assertEqual(
                [message["text"] for message in result["messages"]],
                ["Hello from ChatGPT", "Hello back"],
            )

    def test_import_requires_explicit_consent_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, store = Path(tmp) / "export.json", Path(tmp) / "store.json"
            source.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}), encoding="utf-8")
            importer = ConversationImportStore(store)
            with self.assertRaises(PermissionError):
                importer.import_file(source)
            self.assertTrue(importer.import_file(source, user_consented=True)["imported"])
            self.assertTrue(importer.import_file(source, user_consented=True)["duplicate"])

    def test_secrets_are_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "export.json"
            source.write_text(json.dumps({"messages": [{"role": "user", "content": "api_key=abc123"}]}), encoding="utf-8")
            preview = ConversationImportStore(Path(tmp) / "store.json").preview(source)
            self.assertIn("[REDACTED]", preview["messages"][0]["text"])

    def test_local_analysis_requires_consent_and_never_claims_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            ollama = FakeOllama()
            pipeline = LocalLearningPipeline(ollama_provider=ollama, dataset_path=Path(tmp) / "train.jsonl")
            messages = [{"role": "user", "text": "Help me debug"}]
            with self.assertRaises(PermissionError):
                pipeline.analyze_conversation(messages)
            result = pipeline.analyze_conversation(messages, user_consented=True)
            self.assertFalse(result["weights_updated"])
            self.assertEqual(result["execution"], "local_inference_only")

    def test_training_example_requires_human_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = LocalLearningPipeline(dataset_path=Path(tmp) / "train.jsonl")
            with self.assertRaises(PermissionError):
                pipeline.approve_example(prompt="Q", completion="A")
            result = pipeline.approve_example(prompt="Q", completion="A", reviewer_approved=True)
            self.assertEqual(result["examples"], 1)

    def test_preview_rejects_symbolic_link_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "export.json"
            link = Path(tmp) / "linked.json"
            source.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}), encoding="utf-8")
            try:
                link.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable on this runner")
            with self.assertRaisesRegex(ValueError, "Symbolic-link"):
                ConversationImportStore(Path(tmp) / "store.json").preview(link)


if __name__ == "__main__":
    unittest.main()
